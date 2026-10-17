import os

# Keep tests hermetic: never initialise Agentverse from a developer's local .env.
# load_dotenv() does not override variables that are already set.
os.environ["AGENT_URI"] = ""
