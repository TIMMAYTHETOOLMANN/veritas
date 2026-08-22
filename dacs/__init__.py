"""
DACS — Dynamic Agent Capability System
Auto-discovers, indexes, and intelligently allocates skills/tools when roadblocks are encountered.
"""
from dacs.core.engine import DACSEngine
from dacs.core.indexer import SkillIndexer
from dacs.core.allocator import CapabilityAllocator
from dacs.core.healer import SelfHealer
from dacs.core.mcp_registry import MCPRegistry

__all__ = [
    "DACSEngine",
    "SkillIndexer", 
    "CapabilityAllocator",
    "SelfHealer",
    "MCPRegistry",
]