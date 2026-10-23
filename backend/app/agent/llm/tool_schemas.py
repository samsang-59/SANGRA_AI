"""The three read-only tools as the model sees them (OpenAI function format, which Groq uses).
These are the ONLY tools the model is ever offered."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Find files whose contents or names contain a text (case-insensitive). Returns at most "
                           "20 files with hit counts, most hits first. Config files are included.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The text to look for (1-200 characters)."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_file",
            "description": "The lines of one file that contain a text (case-insensitive), with 2 lines around each.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the project folder."},
                    "query": {"type": "string", "description": "The text to look for."},
                },
                "required": ["path", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_lines",
            "description": "Exact lines of a file, 1-based and both ends included, at most 200 lines per call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the project folder."},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
]

TOOL_NAMES = frozenset(schema["function"]["name"] for schema in TOOL_SCHEMAS)
