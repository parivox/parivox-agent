"""Build agents that run on your own LiveKit project and join Parivox rooms.

    parivox = ParivoxRoom(ctx)
    agent = MyAgent(parivox)            # a ParivoxAgent subclass
    await parivox.start(AgentSession(...), agent)

See https://github.com/parivox/parivox-agent for the guide and an example.
"""

from .agent import ParivoxAgent
from .client import ParivoxApiError, ParivoxClient, RoomMemory, RoomMemoryList, SharedFile
from .metadata import HandoffContact, ParivoxJob, Persona
from .protocol import PROTOCOL_VERSION, PresenceState
from .room import ParivoxRoom
from .secrets import ProviderKeys

__version__ = "0.1.0"

__all__ = [
    "PROTOCOL_VERSION",
    "HandoffContact",
    "ParivoxAgent",
    "ParivoxApiError",
    "ParivoxClient",
    "ParivoxJob",
    "ParivoxRoom",
    "Persona",
    "PresenceState",
    "ProviderKeys",
    "RoomMemory",
    "RoomMemoryList",
    "SharedFile",
]
