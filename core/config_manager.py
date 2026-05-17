import os
import yaml
import datetime
import pytz
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from pathlib import Path

# ==============================================================================
# SULLA V1 - CONFIGURATION & SECRETS MANAGER (The Rulebook)
# Handles loading TradFi Alpaca keys, reading dynamic trading parameters,
# and providing centralized Alpaca client instances to all modules.
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

# Centralized Alpaca clients (lazy-initialized, shared across all modules)
_trading_client = None
_data_client = None

def get_trading_client():
    """Returns the shared Alpaca TradingClient with Omada network armor."""
    global _trading_client
    if _trading_client is None:
        from alpaca.trading.client import TradingClient
        _trading_client = TradingClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            paper=True
        )

        # --- THE NETWORK ARMOR ---
        # Tells the underlying session to retry on brief connection drops
        retry_strategy = Retry(
            total=3,           # Try 3 times before actually throwing a timeout
            backoff_factor=1,  # Wait 1s, 2s, 4s between retries
            status_forcelist=[429, 500, 502, 503, 504],
            # CRITICAL: We only auto-retry GET requests (fetching data/status).
            # We NEVER auto-retry POST (submitting orders) to prevent double-buys.
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        _trading_client._session.mount("https://", adapter)
        _trading_client._session.mount("http://", adapter)

    return _trading_client

def get_data_client():
    """Returns the shared Alpaca HistoricalDataClient with Omada network armor."""
    global _data_client
    if _data_client is None:
        from alpaca.data.historical import StockHistoricalDataClient
        _data_client = StockHistoricalDataClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY")
        )

        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        _data_client._session.mount("https://", adapter)
        _data_client._session.mount("http://", adapter)

    return _data_client

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
    Calculates the current trailing stop multiplier based on Config.yaml.
    Expands the trailing stop buffer during the final hour of trading 
    (3:00 PM - 4:00 PM ET) to survive institutional 'Power Hour' volatility.
    """
    base_multiplier = config.get('ratchet', {}).get('trailing_stop_mult', 2.5)
    ph_settings = config.get('ratchet', {}).get('power_hour_defense', {})
    
    if ph_settings.get('enabled', True):
        ny_tz = pytz.timezone('America/New_York')
        now_ny = datetime.datetime.now(ny_tz)
        
        # Power Hour: 15:00 to 15:59 Eastern Time
        if now_ny.hour == 15:
            buffer = ph_settings.get('atr_buffer', 0.5)
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
