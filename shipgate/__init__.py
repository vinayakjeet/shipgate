"""ShipGate: versioned eval datasets, a calibrated judge, and a CI regression gate."""

from dotenv import load_dotenv

# Provider credentials are read from os.environ by llm/providers/base.py, because
# in CI they arrive as real environment variables. Locally they live in .env,
# which pydantic-settings parses for Settings but never exports.
#
# This lives in the package root rather than in config.py because most import
# paths never touch config: shipgate.runners.judge reaches the provider through
# the llm package without it, so a key sitting in .env stayed invisible.
# override=False keeps real environment variables winning, so CI is unchanged.
load_dotenv(override=False)
