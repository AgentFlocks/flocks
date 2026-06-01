"""
Text chunking for memory system

Implements token-based text chunking with overlap strategy.
Based on OpenClaw's chunking algorithm.
"""

from typing import List

from flocks.memory.types import MemoryChunk
from flocks.memory.config import MemoryChunkingConfig
from flocks.memory.utils.hash import compute_text_hash
from flocks.utils.log import Log

log = Log.create(service="memory.chunking")


class TextChunker:
    """Text chunker with token-based splitting"""
    
    def __init__(self, config: MemoryChunkingConfig):
        """
        Initialize chunker

        Args:
            config: Chunking configuration
        """
        self.config = config
        # Use a deterministic offline heuristic so memory sync never depends
        # on tokenizer assets or outbound network access.
        self.encoding = None
    
    def chunk_text(self, text: str, file_path: str = "") -> List[MemoryChunk]:
        """
        Split text into chunks with overlap
        
        Args:
            text: Text to chunk
            file_path: File path (for logging)
            
        Returns:
            List of memory chunks
        """
        lines = text.splitlines()
        
        if not lines:
            return []
        
        chunks = []
        current_chunk_lines = []
        current_tokens = 0
        start_line = 1
        
        for line_idx, line in enumerate(lines, start=1):
            line_tokens = self._count_tokens(line)
            
            # Check if adding this line would exceed the limit
            if current_tokens + line_tokens > self.config.tokens and current_chunk_lines:
                # Save current chunk
                chunk_text = "\n".join(current_chunk_lines)
                chunks.append(MemoryChunk(
                    start_line=start_line,
                    end_line=line_idx - 1,
                    text=chunk_text,
                    hash=compute_text_hash(chunk_text),
                ))
                
                # Calculate overlap: keep last N lines that fit in overlap budget
                overlap_lines = self._calculate_overlap(current_chunk_lines)
                current_chunk_lines = overlap_lines
                current_tokens = sum(self._count_tokens(line) for line in overlap_lines)
                start_line = line_idx - len(overlap_lines)
            
            # Add current line
            current_chunk_lines.append(line)
            current_tokens += line_tokens
        
        # Save last chunk
        if current_chunk_lines:
            chunk_text = "\n".join(current_chunk_lines)
            chunks.append(MemoryChunk(
                start_line=start_line,
                end_line=len(lines),
                text=chunk_text,
                hash=compute_text_hash(chunk_text),
            ))
        
        log.debug("text.chunked", {
            "file": file_path,
            "lines": len(lines),
            "chunks": len(chunks),
        })
        
        return chunks
    
    def _count_tokens(self, text: str) -> int:
        """
        Count tokens in text

        Args:
            text: Text to count

        Returns:
            Number of tokens
        """
        if self.encoding:
            try:
                return len(self.encoding.encode(text))
            except Exception as e:
                log.warn("chunking.token_count.failed", {"error": str(e)})
        
        # Fallback: approximate 4 chars per token
        return len(text) // 4
    
    def _calculate_overlap(self, lines: List[str]) -> List[str]:
        """
        Calculate overlap lines from end of chunk
        
        Takes lines from the end until overlap token budget is reached.
        
        Args:
            lines: Lines from current chunk
            
        Returns:
            Lines to include in overlap
        """
        if not lines:
            return []
        
        overlap_tokens = 0
        overlap_lines = []
        
        # Iterate from end backwards
        for line in reversed(lines):
            line_tokens = self._count_tokens(line)
            
            # Stop if we exceed overlap budget
            if overlap_tokens + line_tokens > self.config.overlap:
                break
            
            overlap_lines.insert(0, line)
            overlap_tokens += line_tokens
        
        return overlap_lines
