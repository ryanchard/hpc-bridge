-- hpc-bridge fake cluster: a module-served CPython 3.11 (uv-managed, under /opt) — the jail's Python
help([[CPython 3.11 (module-served)]])
whatis("Name: python")
whatis("Version: 3.11")
prepend_path("PATH", "/opt/python/3.11/bin")
setenv("HPCB_MODULE_PYTHON", "3.11")
