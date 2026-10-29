"""Launch the independent email MCP from any working directory."""
import asyncio
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from voicehub.email_mcp import serve
if __name__=='__main__':asyncio.run(serve())
