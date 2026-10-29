"""Opt-in user-owned stdio servers. Upload validates bytes; enabling executes trusted code."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
import zipfile

from jsonschema import Draft202012Validator, ValidationError, SchemaError
from .mcp_client import CommonToolsClient, MCPError
from .mcp_http import HttpToolsClient, checked_remote

TOOL_NAME=re.compile(r'[a-zA-Z0-9_.-]{1,128}')
NAME=re.compile(r'[a-zA-Z][a-zA-Z0-9_-]{0,31}')


def checked_schema(schema):
    if not isinstance(schema,dict) or schema.get('type')!='object':raise ValueError('tool_schema_object_required')
    if len(json.dumps(schema))>16000:raise ValueError('tool_schema_limit')
    def walk(value,depth=0):
        if depth>12:raise ValueError('tool_schema_depth')
        if isinstance(value,dict):
            if any(key in value for key in ('$ref','$dynamicRef','$recursiveRef','pattern','patternProperties')):
                raise ValueError('tool_schema_unsupported_reference_or_pattern')
            for item in value.values():walk(item,depth+1)
        elif isinstance(value,list):
            if len(value)>100:raise ValueError('tool_schema_limit')
            for item in value:walk(item,depth+1)
    walk(schema)
    try:Draft202012Validator.check_schema(schema)
    except SchemaError:raise ValueError('tool_schema_invalid') from None
    return schema


def validate_schema_arguments(schema,arguments):
    if not isinstance(arguments,dict) or len(json.dumps(arguments))>4096:raise ValueError('tool_arguments_limit')
    try:Draft202012Validator(schema).validate(arguments)
    except ValidationError:raise ValueError('tool_arguments_invalid') from None


class ExtensionManager:
    def __init__(self,root):
        self.root=Path(root)/'mcp-extensions';self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'registry.json';self.lock=asyncio.Lock()
        self.records=json.loads(self.path.read_text()) if self.path.exists() else {}
        self.active={}

    def _save(self):
        fd,name=tempfile.mkstemp(dir=self.root,prefix='.registry-')
        try:
            with os.fdopen(fd,'w') as file:json.dump(self.records,file,ensure_ascii=False)
            os.replace(name,self.path)
        finally:
            if os.path.exists(name):os.unlink(name)

    def public_servers(self):
        return [dict(id=r['id'],name=r['name'],kind=r['kind'],transport=r.get('transport','stdio'),enabled=r['enabled'],
                     verified=bool(r.get('tools')),tool_count=len(r.get('tools',[])),sha256=r['sha256'],
                     command=(r.get('command') or [''])[0],argument_count=len(r.get('command',[]))-1 if r.get('command') else 0,
                     url=r.get('url'),protocol_version=r.get('protocol_version'),allow_private=r.get('allow_private',False),
                     authentication_headers=sorted(r.get('headers',{})),
                     environment_keys=sorted(r.get('env',{})),error=r.get('error')) for r in self.records.values()]

    def catalogue(self,enabled_only=True):
        return [{**tool,'enabled':r['enabled'],'server_id':r['id']} for r in self.records.values()
                if r['enabled'] or not enabled_only for tool in r.get('tools',[])]

    def import_bytes(self,raw):
        if len(raw)>2*1024*1024:raise ValueError('upload_size_limit')
        files=None;remote=None
        if raw.startswith(b'PK'):
            files={};total=0
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    if len(archive.infolist())>50:raise ValueError('archive_file_limit')
                    seen=set()
                    for entry in archive.infolist():
                        path=PurePosixPath(entry.filename)
                        canonical=unicodedata.normalize('NFC',path.as_posix()).casefold()
                        if (len(entry.filename)>512 or any(len(part)>100 for part in path.parts) or
                            path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or
                            '\0' in entry.filename or stat.S_ISLNK(entry.external_attr>>16) or
                            canonical in seen):raise ValueError('archive_unsafe_path')
                        seen.add(canonical)
                        if entry.is_dir():continue
                        if path.suffix.lower() not in ('.py','.json','.md','.txt'):raise ValueError('archive_file_type')
                        total+=entry.file_size
                        if total>2*1024*1024:raise ValueError('archive_size_limit')
                        files[str(path)]=archive.read(entry)
            except (zipfile.BadZipFile,RuntimeError):raise ValueError('archive_invalid') from None
            if 'mcp.json' not in files:raise ValueError('archive_manifest_required')
            if len(files['mcp.json'])>32768:raise ValueError('manifest_size_limit')
            manifest=json.loads(files['mcp.json'])
            if not isinstance(manifest,dict) or set(manifest)-{'id','name','entrypoint'}:raise ValueError('manifest_invalid')
            identity=manifest.get('id');entrypoint=manifest.get('entrypoint')
            if entrypoint not in files or not entrypoint.endswith('.py'):raise ValueError('entrypoint_invalid')
            command=None;environment={};kind='uploaded_python'
        else:
            if len(raw)>32768:raise ValueError('manifest_size_limit')
            manifest=json.loads(raw)
            if not isinstance(manifest,dict) or set(manifest)!={'mcpServers'}:raise ValueError('manifest_invalid')
            servers=manifest['mcpServers']
            if not isinstance(servers,dict) or len(servers)!=1:raise ValueError('one_server_per_upload')
            identity,definition=next(iter(servers.items()))
            if isinstance(definition,dict) and definition.get('type') in ('http','streamable-http'):
                remote=checked_remote(definition);command=None;environment={};kind='remote_http'
            else:
                if not isinstance(definition,dict) or set(definition)-{'command','args','env','type'}:raise ValueError('server_config_invalid')
                if definition.get('type','stdio')!='stdio':raise ValueError('only_stdio_transport_supported')
                executable=definition.get('command','');arguments=definition.get('args',[])
                environment=definition.get('env',{})
                if (not isinstance(executable,str) or not Path(executable).is_absolute() or
                    not Path(executable).is_file() or not os.access(executable,os.X_OK)):
                    raise ValueError('absolute_executable_required')
                if (not isinstance(arguments,list) or len(arguments)>50 or
                    any(not isinstance(arg,str) or len(arg)>2048 or '\0' in arg for arg in arguments)):
                    raise ValueError('server_arguments_invalid')
                if (not isinstance(environment,dict) or len(environment)>30 or any(
                    not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}',key) or not isinstance(value,str)
                    or len(value)>4096 or '\0' in value for key,value in environment.items())):
                    raise ValueError('server_environment_invalid')
                command=[executable,*arguments];kind='external_stdio'
        if not isinstance(identity,str) or not NAME.fullmatch(identity) or identity=='common-tools':raise ValueError('server_id_invalid')
        if identity in self.records:raise ValueError('server_already_exists')
        if len(self.records)>=20:raise ValueError('server_count_limit')
        name=manifest.get('name',definition.get('name',identity) if remote else identity)
        if not isinstance(name,str) or not 1<=len(name)<=80:raise ValueError('server_name_invalid')
        directory=self.root/identity
        if directory.exists():raise ValueError('server_directory_exists')
        if files:
            directory.mkdir(mode=0o700)
            try:
                for path,content in files.items():
                    dest=directory/path;dest.parent.mkdir(parents=True,exist_ok=True)
                    dest.write_bytes(content);dest.chmod(0o600)
                command=[sys.executable,str(directory/entrypoint)]
            except Exception:
                shutil.rmtree(directory);raise
        record=dict(id=identity,name=name,kind=kind,command=command,env=environment,
                    cwd=str(directory if files else self.root),enabled=False,tools=[],
                    sha256=hashlib.sha256(raw).hexdigest())
        if remote:record.update(remote,transport='streamable_http')
        self.records[identity]=record;self._save()
        return next(r for r in self.public_servers() if r['id']==identity)

    def client(self,record,timeout=10):
        if record['kind']=='remote_http':return HttpToolsClient(record,timeout)
        return CommonToolsClient(timeout=timeout,command=record['command'],cwd=record['cwd'],env=record['env'])

    def _tools(self,record,tools):
        if not isinstance(tools,list) or not 1<=len(tools)<=20:raise ValueError('server_tool_count')
        result=[];seen=set()
        for tool in tools:
            if not isinstance(tool,dict):raise ValueError('server_tool_invalid')
            name=tool.get('name')
            if not isinstance(name,str) or not TOOL_NAME.fullmatch(name) or name in seen:raise ValueError('server_tool_name')
            seen.add(name);schema=checked_schema(tool.get('inputSchema'))
            description=tool.get('description','')
            if not isinstance(description,str) or len(description)>1000:raise ValueError('server_tool_description')
            annotations=tool.get('annotations',{})
            if not isinstance(annotations,dict):raise ValueError('server_tool_annotations')
            result.append(dict(name='ext_'+record['id']+'__'+name,description=description,inputSchema=schema,
                               annotations={'readOnlyHint':annotations.get('readOnlyHint') is True},
                               remote_name=name))
        return result

    async def enable(self,identity,trust):
        if trust is not True:raise ValueError('explicit_code_trust_required')
        async with self.lock:
            record=self.records[identity]
            try:
                async with asyncio.timeout(12),self.client(record) as client:
                    tools=self._tools(record,await client.list_tools())
                    others=[t for r in self.records.values() if r is not record and r['enabled'] for t in r.get('tools',[])]
                    if len(others+tools)>30 or len(json.dumps(others+tools))>40000:
                        raise ValueError('enabled_catalogue_limit')
            except asyncio.CancelledError:raise
            except (OSError,ValueError,TypeError,KeyError,MCPError,TimeoutError) as exc:
                record.update(enabled=False,error=str(exc) if isinstance(exc,MCPError) else 'verification_failed');self._save()
                raise ValueError(str(exc) if isinstance(exc,MCPError) else 'verification_failed_check_server_dependencies_and_stdio') from None
            record.update(enabled=True,tools=tools,error=None);self._save()

    async def disable(self,identity,delete=False):
        async with self.lock:
            record=self.records[identity]
            record['enabled']=False
            await asyncio.gather(*(c.close() for c in list(self.active.get(identity,()))))
            if delete:
                directory=self.root/identity
                if record['kind']=='uploaded_python' and directory.exists():shutil.rmtree(directory)
                del self.records[identity]
            self._save()

    async def call(self,name,arguments,timeout=20):
        record=next((r for r in self.records.values() if r['enabled'] and any(t['name']==name for t in r['tools'])),None)
        if record is None:raise MCPError('tool_disabled_or_unknown')
        tool=next(t for t in record['tools'] if t['name']==name)
        validate_schema_arguments(tool['inputSchema'],arguments)
        client=self.client(record,timeout)
        self.active.setdefault(record['id'],set()).add(client)
        try:
            async with asyncio.timeout(timeout),client:
                discovered=self._tools(record,await client.list_tools())
                if discovered!=record['tools']:raise MCPError('tool_definition_changed_revalidate')
                if not record['enabled']:raise MCPError('tool_disabled')
                result=await client.call(tool['remote_name'],arguments)
                if not record['enabled']:raise MCPError('tool_disabled')
            return result
        finally:self.active[record['id']].discard(client)

    async def close(self):
        await asyncio.gather(*(client.close() for clients in self.active.values() for client in list(clients)))
