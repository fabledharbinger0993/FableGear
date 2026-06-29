# Checkpoint/Resume System Implementation Guide

## Overview

This guide provides step-by-step instructions for implementing the universal checkpoint/resume system across all FableGear tools as specified in the enhancement request.

## Current State

The project already has:
- Basic checkpoint infrastructure in `checkpoint.py`
- New checkpoint manager foundation in `checkpoint_manager/`
- Some tools already have checkpoint parameters (e.g., `duplicate_detector.py`)

## Implementation Steps

### Phase 1: Complete Checkpoint Manager Integration

#### 1.1 Fix Import Dependencies
The new checkpoint manager needs to properly integrate with the existing `checkpoint.py`:

```python
# In checkpoint_manager/base.py
try:
    from checkpoint import Checkpoint, check_checkpoint
except ImportError:
    # Fallback for testing
    Checkpoint = None
    check_checkpoint = None
```

#### 1.2 Update Tool Implementations
Modify existing tools to use the new checkpoint manager:

**For duplicate_detector.py:**
- Already has `checkpoint` parameter in `scan_duplicates()`
- Wrap checkpoint calls with new `DuplicatesCheckpoint` class
- Add auto-checkpoint every N files

**For other Chop Shop tools:**
- Add checkpoint parameter to main function signatures
- Implement tool-specific checkpoint classes
- Add auto-checkpoint intervals based on operation cost

#### 1.3 CLI Integration
Add `--resume-from` flag to all CLI tools:

```python
# In cli.py for each tool
parser.add_argument(
    "--resume-from",
    action="store_true",
    help="Resume from last checkpoint"
)
```

### Phase 2: Pipeline Wizard Integration

#### 2.1 Register All Tools
Update `pipeline_wizard/tool_registry.py` to include:
- All Record Room tools (audit, import, fix_paths, link_playlists)
- All Chop Shop tools (tag, duplicates, rename, organize, normalize, convert, novelty)

#### 2.2 Flask Routes
Add API endpoints in `routes_tools.py`:
- `/api/pipeline/create` - Create pipeline configuration
- `/api/pipeline/run` - Execute pipeline
- `/api/pipeline/checkpoint/list` - List available checkpoints
- `/api/pipeline/checkpoint/resume` - Resume from checkpoint

#### 2.3 UI Components
Create frontend components in `static/`:
- Pipeline builder interface
- Progress visualization
- Checkpoint management UI
- Tool configuration panels

### Phase 3: Testing Strategy

#### 3.1 Unit Tests
Create tests in `tests/test_checkpoint_manager.py`:
- Test checkpoint creation and loading
- Test validation logic
- Test cleanup functionality
- Test tool-specific implementations

#### 3.2 Integration Tests
Create tests in `tests/test_pipeline_wizard.py`:
- Test pipeline execution in all modes
- Test checkpoint creation during pipeline
- Test resume functionality
- Test error handling and rollback

### Phase 4: UI Integration

#### 4.1 Main App Integration
Add to `app.py`:
- Import pipeline wizard modules
- Register pipeline blueprints
- Add startup checkpoint check

#### 4.2 Progress Indicators
Update SSE streaming in `helpers.py`:
- Add pipeline progress events
- Add checkpoint save notifications
- Add resume prompts

## Success Criteria

- [ ] All FableGear tools support checkpoint/resume
- [ ] Pipeline Wizard includes all tools
- [ ] Checkpoints are created automatically at meaningful intervals
- [ ] Users can manually create checkpoints
- [ ] Resume flow is intuitive and reliable
- [ ] Checkpoints are auto-cleaned after 30 days
- [ ] Pipeline can run end-to-end without manual intervention
- [ ] Tools can be safely interrupted and resumed

## Next Steps

1. **Immediate**: Integrate checkpoint manager with existing tools
2. **Short-term**: Complete Pipeline Wizard UI and API
3. **Medium-term**: Implement multi-drive processing
4. **Long-term**: Implement three-view library browser and auto-update system

## Example Integration Pattern

```python
# In a tool file (e.g., audio_processor.py)
from checkpoint_manager import TagTracksCheckpoint

def tag_tracks(roots, config, resume_from=False):
    # Initialize checkpoint manager
    ckpt = TagTracksCheckpoint(roots, config)
    
    # Check for existing checkpoint
    if resume_from and ckpt.has_checkpoint():
        state = ckpt.load_checkpoint()
        processed_files = state.get("tool_state", {}).get("processed_files", [])
        start_index = len(processed_files)
    else:
        processed_files = []
        start_index = 0
    
    # Process files
    for i, file in enumerate(files[start_index:], start_index):
        # Process file...
        processed_files.append(file)
        
        # Auto-checkpoint every 50 files
        if ckpt.auto_checkpoint({
            "tool_state": {
                "processed_files": processed_files,
                "failed_files": [],
            }
        }):
            print(f"Checkpoint saved at {i} files")
    
    # Manual checkpoint if user requested
    if user_requested_checkpoint:
        ckpt.manual_checkpoint({
            "tool_state": {
                "processed_files": processed_files,
                "failed_files": [],
            }
        })
    
    # Cleanup on completion
    ckpt.cleanup()
```

## Safety Considerations

- Always validate checkpoint data before loading
- Check for data corruption before resuming
- Preserve user configuration across checkpoints
- Never checkpoint during sensitive database operations
- Allow users to opt-out of checkpointing