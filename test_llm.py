from edgedash.config import load_config
from edgedash.llm import complete_json

config = load_config()

result = complete_json(
    'Return exactly this JSON: {"status": "ok"}',
    {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {
                "type": "string"
            }
        }
    },
    config=config,
)

print(result)