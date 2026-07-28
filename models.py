from pydantic import BaseModel

class CmdRequest(BaseModel):
    cmd: str

class ResetHardwareRequest(BaseModel):
    confirm: str
