"""
Test ID generation compatibility with TypeScript implementation
"""

import pytest
import time
from flocks.utils.id import Identifier


class TestIdentifierCompatibility:
    """Test that Python ID generation matches TypeScript exactly"""
    
    def test_prefix_mappings(self):
        """Test that prefixes match TypeScript exactly"""
        expected_prefixes = {
            "session": "ses",
            "message": "msg",
            "permission": "per",
            "question": "que",
            "user": "usr",
            "part": "prt",
            "pty": "pty",
            "tool": "tool",
            # Added in Batch 4 for MessageV2 and Session advanced features
            "slug": "slg",
            "call": "cal",
            "step": "stp",
            "agent": "agt",
            "subtask": "stk",
            "event": "evt",
            "task": "tsk",
            "texec": "txe",
        }
        
        assert Identifier._prefixes == expected_prefixes
    
    def test_id_format(self):
        """Test ID format: {prefix}_{hex_time(12)}{random_base62(14)}"""
        id_str = Identifier.ascending("session")
        
        # Check format
        assert "_" in id_str
        prefix, id_part = id_str.split("_", 1)
        
        # Check prefix
        assert prefix == "ses"
        
        # Check length (12 hex + 14 base62 = 26)
        assert len(id_part) == 26
        
        # Check hex part (first 12 characters should be valid hex)
        hex_part = id_part[:12]
        assert all(c in "0123456789abcdef" for c in hex_part)
        
        # Check random part (last 14 characters should be base62)
        random_part = id_part[12:]
        assert len(random_part) == 14
        assert all(c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for c in random_part)
    
    def test_ascending_ids_are_monotonic(self):
        """Test that ascending IDs are chronologically ordered"""
        ids = []
        for _ in range(10):
            ids.append(Identifier.ascending("session"))
            time.sleep(0.001)  # Small delay
        
        # IDs should be in ascending order
        assert ids == sorted(ids)
    
    def test_descending_ids_are_reverse_monotonic(self):
        """Test that descending IDs are reverse chronologically ordered"""
        ids = []
        for _ in range(10):
            ids.append(Identifier.descending("session"))
            time.sleep(0.001)  # Small delay
        
        # IDs should be in descending order
        assert ids == sorted(ids, reverse=True)
    
    def test_timestamp_extraction(self):
        """
        Test extracting timestamp from ascending ID
        
        Note: timestamp() returns a truncated value (48-bit limitation),
        so it's only useful for comparing relative order, not absolute time.
        """
        id1 = Identifier.ascending("session")
        time.sleep(0.01)  # Small delay
        id2 = Identifier.ascending("session")
        
        ts1 = Identifier.timestamp(id1)
        ts2 = Identifier.timestamp(id2)
        
        # Timestamps should be ordered (id2 created after id1)
        assert ts2 > ts1
    
    def test_timestamp_extraction_for_comparison(self):
        """
        Test timestamp extraction for comparing IDs
        
        This is the actual use case in TypeScript - comparing relative times,
        not extracting absolute timestamps.
        """
        # Create ID with known timestamp
        fixed_ts = 1706000000000
        id_str = Identifier.create("session", descending=False, timestamp=fixed_ts)
        
        # Create cutoff ID (1 hour earlier)
        cutoff_ts = fixed_ts - (60 * 60 * 1000)
        cutoff_id = Identifier.create("session", descending=False, timestamp=cutoff_ts)
        
        # Extract timestamps
        extracted = Identifier.timestamp(id_str)
        cutoff_extracted = Identifier.timestamp(cutoff_id)
        
        # Comparison should work (newer ID has larger timestamp)
        assert extracted > cutoff_extracted
    
    def test_counter_mechanism(self):
        """Test that counter increments for same timestamp"""
        fixed_ts = 1706000000000
        
        # Generate multiple IDs with same timestamp
        ids = []
        for _ in range(5):
            ids.append(Identifier.create("session", descending=False, timestamp=fixed_ts))
        
        # All IDs should be different (due to counter)
        assert len(set(ids)) == 5
        
        # IDs should be in order
        assert ids == sorted(ids)
    
    def test_schema_validation(self):
        """Test Pydantic schema validation"""
        from pydantic import BaseModel, ValidationError
        
        class TestModel(BaseModel):
            session_id: Identifier.schema("session")
        
        # Valid ID should pass
        valid_id = Identifier.ascending("session")
        model = TestModel(session_id=valid_id)
        assert model.session_id == valid_id
        
        # Invalid prefix should fail
        with pytest.raises(ValidationError):
            TestModel(session_id="msg_0123456789ab0123456789ab")
        
        # Invalid format should fail
        with pytest.raises(ValidationError):
            TestModel(session_id="ses_invalid")
    
    def test_parse_method(self):
        """Test ID parsing"""
        id_str = Identifier.ascending("session")
        prefix, id_part = Identifier.parse(id_str)
        
        assert prefix == "ses"
        assert len(id_part) == 26
    
    def test_validate_method(self):
        """Test ID validation"""
        # Valid ID
        valid_id = Identifier.ascending("session")
        assert Identifier.validate(valid_id, "session") is True
        
        # Wrong prefix
        msg_id = Identifier.ascending("message")
        assert Identifier.validate(msg_id, "session") is False
        
        # Invalid format
        assert Identifier.validate("invalid", "session") is False
    
    def test_given_id_validation(self):
        """Test ascending/descending with given ID"""
        # Valid given ID
        id_str = Identifier.ascending("session")
        assert Identifier.ascending("session", id_str) == id_str
        
        # Invalid prefix should raise
        with pytest.raises(ValueError):
            Identifier.ascending("session", "msg_0123456789ab0123456789ab")
    
    def test_all_prefix_types(self):
        """Test all prefix types work"""
        prefixes = ["session", "message", "permission", "question", "user", "part", "pty", "tool"]
        
        for prefix in prefixes:
            # Should generate without error
            id_asc = Identifier.ascending(prefix)
            id_desc = Identifier.descending(prefix)
            
            # Should have correct prefix
            assert id_asc.startswith(Identifier._prefixes[prefix] + "_")
            assert id_desc.startswith(Identifier._prefixes[prefix] + "_")
            
            # Should validate
            assert Identifier.validate(id_asc, prefix)
            assert Identifier.validate(id_desc, prefix)
    
    def test_id_uniqueness(self):
        """Test that generated IDs are unique"""
        ids = set()
        for _ in range(1000):
            id_str = Identifier.ascending("session")
            assert id_str not in ids, "Duplicate ID generated!"
            ids.add(id_str)
    
    def test_typescript_compatibility_example(self):
        """
        Test with example from TypeScript to ensure format matches
        
        TypeScript example:
        ses_0123456789ab0123456789ab (26 chars after prefix)
        """
        id_str = Identifier.ascending("session")
        
        # Should match pattern: ses_{hex(12)}{base62(14)}
        assert id_str.startswith("ses_")
        id_part = id_str[4:]  # Remove "ses_"
        assert len(id_part) == 26
        
        # Hex part
        hex_part = id_part[:12]
        int(hex_part, 16)  # Should parse as hex
        
        # Base62 part
        base62_part = id_part[12:]
        assert len(base62_part) == 14


class TestBackwardCompatibility:
    """Test backward compatibility considerations"""
    
    def test_old_ulid_format_detection(self):
        """Test that we can detect old ULID format (for migration)"""
        # Old format: session_01ARZ3NDEKTSV4RRFFQ69G5FAV
        old_id = "session_01ARZ3NDEKTSV4RRFFQ69G5FAV"
        
        # Should not validate with new format
        assert not Identifier.validate(old_id, "session")
        
        # Prefix is wrong too
        prefix, _ = Identifier.parse(old_id)
        assert prefix == "session"  # Old format used full prefix
        assert prefix != Identifier._prefixes["session"]  # New uses "ses"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
