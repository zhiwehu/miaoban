"""No dependencies, filesystem writes or network. Example line-delimited stdio MCP."""
import json
import sys

for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request:continue
    method=request.get('method')
    if method=='initialize':
        result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},
                'serverInfo':{'name':'local-echo','version':'1.0'}}
    elif method=='tools/list':
        result={'tools':[{'name':'echo','description':'将用户明确提供的文字原样返回，用于验证用户上传的 MCP 和参数传递。',
            'inputSchema':{'type':'object','properties':{'text':{'type':'string','maxLength':200}},
                           'required':['text'],'additionalProperties':False},
            'annotations':{'readOnlyHint':True}}]}
    elif method=='tools/call':
        arguments=request.get('params',{}).get('arguments',{})
        result={'content':[{'type':'text','text':json.dumps({'echo':arguments.get('text')},ensure_ascii=False)}],
                'isError':False}
    else:
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32601,'message':'Unknown method'}}),flush=True)
        continue
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result},ensure_ascii=False),flush=True)
