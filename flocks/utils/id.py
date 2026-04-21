"""
ID generation utility

Provides identifier generation exactly matching Flocks' TypeScript implementation.
This ensures complete compatibility between Python and TypeScript services.
"""

import secrets
import time
from typing import Literal, Optional
from pydantic import Field


# Define ID prefix types - matches TypeScript exactly
IdPrefix = Literal[
    "session",    # ses
    "message",    # msg
    "permission", # per
    "question",   # que
    "audit",      # aud
    "user",       # usr
    "part",       # prt
    "pty",        # pty
    "tool",       # tool
    "slug",       # slg
    "call",       # cal
    "step",       # stp
    "agent",      # agt
    "subtask",    # stk
    "event",      # evt
    "tqref",      # tqr
    "chbind",     # chb  (channel session binding)
]


class Identifier:
    """
    Identifier utility class
    
    Exactly matches Flocks's TypeScript Identifier namespace.
    Generates monotonic IDs with format: {prefix}_{hex_time}{random_base62}
    """
    
    # Prefix mappings - MUST match TypeScript exactly
    _prefixes = {
        "session": "ses",
        "message": "msg",
        "permission": "per",
        "question": "que",
        "audit": "aud",
        "user": "usr",
        "part": "prt",
        "pty": "pty",
        "tool": "tool",
        "slug": "slg",
        "call": "cal",
        "step": "stp",
        "agent": "agt",
        "subtask": "stk",
        "event": "evt",
        "tqref": "tqr",
        "task": "tsk",
        "texec": "txe",
        "chbind": "chb",
    }
    
    # Constants
    _LENGTH = 26
    
    # State for monotonic ID generation (class variables)
    _last_timestamp = 0
    _counter = 0
    
    @staticmethod
    def _random_base62(length: int) -> str:
        """
        Generate random base62 string
        
        Args:
            length: Length of random string
            
        Returns:
            Random base62 string
        """
        chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        random_bytes = secrets.token_bytes(length)
        result = ""
        for byte in random_bytes:
            result += chars[byte % 62]
        return result
    
    @staticmethod
    def create(prefix: IdPrefix, descending: bool = False, timestamp: Optional[int] = None) -> str:
        """
        Create a new identifier
        
        Args:
            prefix: ID type prefix
            descending: If True, generate descending (reverse-ordered) ID
            timestamp: Optional timestamp in milliseconds (defaults to now)
            
        Returns:
            Generated identifier string
        """
        current_timestamp = timestamp if timestamp is not None else int(time.time() * 1000)
        
        # Update monotonic counter
        if current_timestamp != Identifier._last_timestamp:
            Identifier._last_timestamp = current_timestamp
            Identifier._counter = 0
        Identifier._counter += 1
        
        # Combine timestamp and counter: timestamp * 0x1000 + counter
        # This matches TypeScript: BigInt(currentTimestamp) * BigInt(0x1000) + BigInt(counter)
        now = current_timestamp * 0x1000 + Identifier._counter
        
        # Invert for descending IDs
        if descending:
            # TypeScript uses bitwise NOT which works on all bits
            # We need to simulate this with a large mask
            now = ~now & ((1 << 64) - 1)  # Use 64-bit mask
        
        # Convert to 6-byte hex string (12 hex characters)
        # Extract only the lower 48 bits (6 bytes)
        time_bytes = []
        for i in range(6):
            time_bytes.append((now >> (40 - 8 * i)) & 0xFF)
        time_hex = ''.join(f'{b:02x}' for b in time_bytes)
        
        # Generate random base62 suffix
        random_suffix = Identifier._random_base62(Identifier._LENGTH - 12)
        
        # Combine: prefix_hextime(12)random(14)
        prefix_str = Identifier._prefixes[prefix]
        return f"{prefix_str}_{time_hex}{random_suffix}"
    
    @staticmethod
    def ascending(prefix: IdPrefix, given: Optional[str] = None) -> str:
        """
        Generate or validate an ascending (chronologically ordered) ID
        
        Args:
            prefix: ID type prefix
            given: Optional existing ID to validate
            
        Returns:
            Generated or validated ID
        """
        if given is None:
            return Identifier.create(prefix, descending=False)
        
        prefix_str = Identifier._prefixes[prefix]
        if not given.startswith(f"{prefix_str}_"):
            raise ValueError(f"ID {given} does not start with {prefix_str}_")
        return given
    
    @staticmethod
    def descending(prefix: IdPrefix, given: Optional[str] = None) -> str:
        """
        Generate or validate a descending (reverse chronologically ordered) ID
        
        Args:
            prefix: ID type prefix
            given: Optional existing ID to validate
            
        Returns:
            Generated or validated ID
        """
        if given is None:
            return Identifier.create(prefix, descending=True)
        
        prefix_str = Identifier._prefixes[prefix]
        if not given.startswith(f"{prefix_str}_"):
            raise ValueError(f"ID {given} does not start with {prefix_str}_")
        return given
    
    @staticmethod
    def schema(prefix: IdPrefix):
        """
        Create a Pydantic field schema for an identifier
        
        Args:
            prefix: ID type prefix for validation
            
        Returns:
            Annotated string type for Pydantic models
        """
        from typing import Annotated
        
        prefix_str = Identifier._prefixes[prefix]
        
        return Annotated[
            str,
            Field(
                description=f"Unique identifier for {prefix}",
                pattern=f"^{prefix_str}_[0-9a-f]{{12}}[0-9A-Za-z]{{14}}$"
            )
        ]
    
    @staticmethod
    def timestamp(id_str: str) -> int:
        """
        Extract timestamp from an ascending ID
        
        Note: Does NOT work with descending IDs (they are inverted)
        
        Args:
            id_str: ID string
            
        Returns:
            Timestamp in milliseconds
        """
        # Extract prefix and ID parts
        if "_" not in id_str:
            raise ValueError(f"Invalid ID format: {id_str}")
        
        prefix, id_part = id_str.split("_", 1)
        
        # Extract hex time part (first 12 characters)
        if len(id_part) < 12:
            raise ValueError(f"Invalid ID format: {id_str}")
        
        hex_time = id_part[:12]
        
        # Parse hex to integer (this is the combined timestamp + counter)
        # The hex represents 6 bytes (48 bits)
        encoded = int(hex_time, 16)
        
        # Extract timestamp: divide by 0x1000 to remove counter
        # This matches TypeScript: Number(encoded / BigInt(0x1000))
        # The format is: timestamp * 0x1000 + counter
        return encoded // 0x1000
    
    @staticmethod
    def parse(identifier: str) -> tuple[str, str]:
        """
        Parse an identifier into prefix and ID parts
        
        Args:
            identifier: Full identifier string
            
        Returns:
            Tuple of (prefix, id_part)
        """
        if "_" not in identifier:
            raise ValueError(f"Invalid identifier format: {identifier}")
        
        parts = identifier.split("_", 1)
        return parts[0], parts[1]
    
    @staticmethod
    def validate(identifier: str, expected_prefix: IdPrefix) -> bool:
        """
        Validate an identifier has the expected prefix
        
        Args:
            identifier: Identifier to validate
            expected_prefix: Expected prefix type
            
        Returns:
            True if valid, False otherwise
        """
        try:
            prefix, id_part = Identifier.parse(identifier)
            expected_prefix_str = Identifier._prefixes[expected_prefix]
            
            # Check prefix matches and ID part has correct length
            return (
                prefix == expected_prefix_str and
                len(id_part) == Identifier._LENGTH
            )
        except (ValueError, KeyError):
            return False


# Type aliases for common ID types (using schema)
SessionId = Identifier.schema("session")
MessageId = Identifier.schema("message")
PermissionId = Identifier.schema("permission")
QuestionId = Identifier.schema("question")
UserId = Identifier.schema("user")
PartId = Identifier.schema("part")
PtyId = Identifier.schema("pty")
ToolId = Identifier.schema("tool")
