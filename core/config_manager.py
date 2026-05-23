import os
import yaml
import datetime
import pytz
from dotenv import load_dotenv
from pathlib import Path

# ==============================================================================
# IONIC V1 - CONFIGURATION & SECRETS MANAGER (The Rulebook)
# Loads FX (Oanda) secrets from .env and dynamic trading parameters from
# Config.yaml. The Oanda v20 broker client itself is constructed in the
# broker adapter (Phase 2+); this module is config/secrets only.
# ==============================================================================

BASE_DIR = Path(__file__).parent
# Container-friendly: env overrides for the two on-disk files. In a container,
# secrets typically arrive via compose `env_file:` (so .env may not exist) and
# Config.yaml is bind-mounted from the host so it survives container rebuilds.
ENV_PATH    = Path(os.environ.get('ENV_FILE',    BASE_DIR / '.env'))
CONFIG_PATH = Path(os.environ.get('CONFIG_PATH', BASE_DIR / 'Config.yaml'))

# Load .env once at module import. python-dotenv silently no-ops if the file
# is absent, which is the desired behavior in containers (env arrives via
# compose `env_file:` / `environment:` instead).
load_dotenv(ENV_PATH)

def load_secrets():
    """
    Returns environment variables from the .env file (already loaded at module import).
    """
    return {
        'alpaca_api_key': os.getenv("ALPACA_API_KEY"),
        'alpaca_secret_key': os.getenv("ALPACA_SECRET_KEY"),
        'telegram_bot_token': os.getenv("TELEGRAM_BOT_TOKEN"),
        'telegram_user_id': os.getenv("TELEGRAM_USER_ID"),
        'brave_api_key': os.getenv("BRAVE_API_KEY")
    }

def load_engine_config():
    """
    Reads the Config.yaml file.

    Honors LMSTUDIO_BASE_URL as an environment override for the AI sentiment
    endpoint. In a container the inference endpoint reaches the LM Studio
    host via host.docker.internal; we want operators to swap that without
    editing the bind-mounted Config.yaml.
    """
    try:
        with open(CONFIG_PATH, 'r') as file:
            cfg = yaml.safe_load(file) or {}
    except FileNotFoundError:
        print("❌ ERROR: Config.yaml not found. Check your file paths.")
        return {}
    except yaml.YAMLError as exc:
        print(f"❌ ERROR: Config.yaml is corrupted or formatted incorrectly: {exc}")
        return {}

    lm_url = os.getenv("LMSTUDIO_BASE_URL")
    if lm_url:
        cfg.setdefault('ai_agent', {}).setdefault('sentiment_analysis', {})['api_base'] = lm_url

    return cfg
def get_ratchet_multiplier(config):
    """
    Returns the trailing-stop multiplier, optionally widened during the
    configured high-volatility window. For FX, the canonical window is the
    London/NY overlap (08:00-12:00 ET) where price moves are largest.
    Configurable via ratchet.power_hour_defense in Config.yaml.
    """
    base_multiplier = config.get('ratchet', {}).get('trailing_stop_mult', 2.5)
    ph_settings = config.get('ratchet', {}).get('power_hour_defense', {}) or {}

    if not ph_settings.get('enabled', False):
        return base_multiplier

    start_h = int(ph_settings.get('start_hour_et', 8))
    end_h   = int(ph_settings.get('end_hour_et', 12))
    buffer  = float(ph_settings.get('atr_buffer', 0.3))

    now_ny = datetime.datetime.now(pytz.timezone('America/New_York'))
    if start_h <= now_ny.hour < end_h:
        return base_multiplier + buffer
    return base_multiplier
import copy

def get_symbol_config(global_config, symbol):
    """
    Takes the global Config.yaml dict and dynamically overwrites it
    with any ticker-specific sniper settings before execution.
    """
    # Create a deep copy so we don't permanently alter the global config in memory
    cfg = copy.deepcopy(global_config)

    # Check if this specific symbol has custom sniper settings
    overrides = cfg.get('strategy', {}).get('symbol_overrides', {}).get(symbol, {})

    # If no overrides exist, just return the standard global config
    if not overrides:
        return cfg

    # --- MERGE STRATEGY OVERRIDES ---
    if 'adx_trend_threshold' in overrides:
        cfg['strategy']['adx_trend_threshold'] = overrides['adx_trend_threshold']

    # --- MERGE PARADIGM OVERRIDES ---
    for paradigm, settings in overrides.get('paradigms', {}).items():
        if paradigm in cfg['strategy'].get('paradigms', {}):
            cfg['strategy']['paradigms'][paradigm].update(settings)

    # --- MERGE RATCHET/RISK OVERRIDES ---
    if 'initial_stop_mult' in overrides:
        cfg['ratchet']['initial_stop_mult'] = overrides['initial_stop_mult']
    if 'trailing_stop_mult' in overrides:
        cfg['ratchet']['trailing_stop_mult'] = overrides['trailing_stop_mult']

    return cfg
