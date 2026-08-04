# FableGear Audit Manifest

## Files Generated
- `audit.log` - This script's execution log
- `pip_audit.txt` - Dependency vulnerability scan
- `dependency_tree.txt` - Full dependency tree
- `ruff_check.json` - Linting results
- `ruff_format_diff.txt` - Code formatting violations
- `pyright.json` / `mypy/` - Type safety results
- `module_inventory.txt` - Python module structure
- `import_analysis.txt` - Import graph analysis
- `db_mutations.txt` - Database write operations
- `file_operations.txt` - File I/O operations (rm, unlink, etc)
- `secrets_scan.txt` - Hardcoded secrets scan
- `config_files.txt` - Configuration file inventory
- `test_count.txt` - Test suite structure and count
- `pytest_output.txt` - Test execution results (if pytest available)
- `ci_workflows.txt` - GitHub Actions pipeline definitions
- `platform_check.txt` - Platform compatibility analysis
- `documentation.txt` - Documentation file inventory

## Usage

1. Run this script:
   ```bash
   bash fablegear_audit.sh /path/to/FableGear
   ```

2. Copy the audit directory to your working area

3. Feed to Claude Opus:
   ```
   I'm auditing the FableGear project. Here's the full audit context.
   [Paste AUDIT_CONTEXT.md below]
   ```

## Next Steps

See OPUS_PROMPT.md for the full audit prompt.
