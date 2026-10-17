# Project development

- Run every test and validation script with the MATLAB-compatible Python 3.10
  interpreter: `C:/Users/Administrator/conda-envs/matlab310/python.exe`.
  Do not use the system Python or `venv_build` for tests.
- PowerShell test command:
  `& 'C:/Users/Administrator/conda-envs/matlab310/python.exe' -m pytest -q`
- Preserve existing, unrelated working tree changes; stage only files belonging
  to the current task.
