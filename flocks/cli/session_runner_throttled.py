"""
CLI session runner with throttled UI updates (Optional Advanced Version).

This is an alternative implementation that provides real-time typing effect
while maintaining smoothness through throttling.

To use: Replace the _on_text_delta implementation in session_runner.py
"""

import asyncio
import time
from rich.text import Text


class CLISessionRunnerThrottled:
    """
    Enhanced version with throttled UI updates.
    
    Features:
    - Real-time typing effect
    - Throttled UI updates (max 10 updates/second)
    - No choppy rendering
    - Shows character count during streaming
    """
    
    def __init__(self, *args, **kwargs):
        # Call parent init
        super().__init__(*args, **kwargs)
        
        # Throttling state
        self._last_ui_update = 0
        self._ui_update_interval = 0.1  # 100ms = 10 updates/second
        self._pending_delta_count = 0
    
    async def _on_text_delta(self, delta: str) -> None:
        """
        Handle text delta from LLM with throttled UI updates.
        
        Strategy:
        - Accumulate all deltas in buffer
        - Update UI at most once per 100ms
        - Show character count to indicate progress
        - Balance between responsiveness and performance
        """
        self._content_buffer.append(delta)
        self._has_content = True
        self._pending_delta_count += len(delta)
        
        # Throttle UI updates to max 10/second
        now = time.time()
        if self._live and (now - self._last_ui_update) >= self._ui_update_interval:
            # Calculate stats
            total_chars = sum(len(d) for d in self._content_buffer)
            
            # Show preview of recent content
            recent_text = "".join(self._content_buffer[-100:])  # Last 100 deltas
            preview = recent_text[-40:] if len(recent_text) > 40 else recent_text
            
            # Clean up preview for display
            preview = preview.replace('\n', '↵ ')
            if len(preview) > 40:
                preview = preview[:37] + "..."
            
            # Update with progress info
            status_text = f"Receiving ({total_chars} chars)... {preview}"
            self._live.update(Text(status_text, style="dim italic"))
            
            self._last_ui_update = now
            self._pending_delta_count = 0


# Alternative: Batch processing with async task
class CLISessionRunnerBatched:
    """
    Enhanced version with async batch processing.
    
    Features:
    - Asynchronous batch processing
    - Smoother updates
    - Configurable batch interval
    """
    
    def __init__(self, *args, **kwargs):
        import asyncio
        super().__init__(*args, **kwargs)
        
        # Batch processing state
        self._delta_batch = []
        self._batch_task = None
        self._batch_interval = 0.05  # 50ms batching
        self._batch_running = False
    
    async def _on_text_delta(self, delta: str) -> None:
        """
        Handle text delta with async batch processing.
        """
        self._content_buffer.append(delta)
        self._delta_batch.append(delta)
        self._has_content = True
        
        # Start batch processing task if not running
        if not self._batch_running:
            self._batch_running = True
            asyncio.create_task(self._batch_flush())
    
    async def _batch_flush(self):
        """
        Async task to flush batched deltas to UI.
        """
        import asyncio
        
        while self._batch_running and (self._delta_batch or self._has_content):
            await asyncio.sleep(self._batch_interval)
            
            if self._delta_batch and self._live:
                # Calculate stats
                batch_size = len(self._delta_batch)
                total_chars = sum(len(d) for d in self._content_buffer)
                
                # Show batch info
                status_text = f"Receiving ({total_chars} chars, +{batch_size} updates)..."
                self._live.update(Text(status_text, style="dim italic"))
                
                self._delta_batch.clear()
            
            # Exit if no activity
            if not self._delta_batch:
                break
        
        self._batch_running = False


"""
Usage Instructions:
===================

Method 1: Direct Replacement (Throttled)
-----------------------------------------
In session_runner.py, add these lines to __init__:

    self._last_ui_update = 0
    self._ui_update_interval = 0.1
    self._pending_delta_count = 0

Then replace the _on_text_delta method with the throttled version above.


Method 2: Inheritance (Batched)
--------------------------------
Create a new runner:

    from flocks.cli.session_runner_throttled import CLISessionRunnerBatched
    
    runner = CLISessionRunnerBatched(console, directory, model, agent)
    await runner.start()


Performance Comparison:
=======================

Original (Flocks):
- UI updates: ~30/second (every delta)
- Choppiness: HIGH
- Responsiveness: HIGH
- Terminal IO: HEAVY

Quick Fix (No delta updates):
- UI updates: ~4/second (spinner only)
- Choppiness: NONE
- Responsiveness: LOW (no real-time feedback)
- Terminal IO: LIGHT

Throttled (This file - Method 1):
- UI updates: ~10/second (throttled)
- Choppiness: LOW
- Responsiveness: MEDIUM (100ms lag)
- Terminal IO: MEDIUM

Batched (This file - Method 2):
- UI updates: ~20/second (batched)
- Choppiness: VERY LOW
- Responsiveness: HIGH (50ms lag)
- Terminal IO: MEDIUM

Flocks (Reference):
- UI updates: 0/second during streaming, 1 at end
- Choppiness: NONE
- Responsiveness: NONE during streaming
- Terminal IO: MINIMAL


Recommendation:
===============

1. For most users: Use the quick fix (already applied to session_runner.py)
   - Matches Flocks behavior
   - Eliminates all choppiness
   - Simple and reliable

2. For real-time feedback: Use throttled version (Method 1)
   - Shows progress during streaming
   - Still smooth
   - Slightly more complex

3. For maximum responsiveness: Use batched version (Method 2)
   - Best balance of responsiveness and smoothness
   - More complex implementation
   - Requires async task management
"""
