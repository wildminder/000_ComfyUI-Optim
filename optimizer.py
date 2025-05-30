"""
@author: Wildminder
@title: ComfyUI-Optim
@project: "https://github.com/wildminder/000_ComfyUI-Optim",
@description: patch ComfyUI's internal node loading behavior
"""

import sys
import os
import io
import json
import traceback
import logging

# Set Environment Variable IMMEDIATELY
try:
    ENV_VAR_NAME = "NO_ALBUMENTATIONS_UPDATE"
    ENV_VAR_VALUE = "1"
    os.environ[ENV_VAR_NAME] = ENV_VAR_VALUE
    #print(f"[000_ComfyUI-Optim] INFO: Attempted to set environment variable: {ENV_VAR_NAME}={ENV_VAR_VALUE}")
except Exception as e:
    print(f"[000_ComfyUI-Optim] ERROR: Failed to set environment variable {ENV_VAR_NAME}: {e}")


PATCHER_NAME = "000_ComfyUI-Optim"
CONFIG_FILE_NAME = "optimizer-config.json"
DEFAULT_CONFIG = {
    "modules_to_silence": [],
    "patcher_log_level": "INFO",
    "log_suppressed_output": False,
    "patcher_debug_mode": False
}

config = DEFAULT_CONFIG.copy()

logger = logging.getLogger(PATCHER_NAME)

# Store original functions
original_load_custom_node = None
original_get_module_name = None
comfy_nodes_module = None


def load_patcher_config():
    """Loads configuration from JSON file."""
    global config

    patcher_dir = os.path.dirname(os.path.realpath(__file__))
    config_file_path_local = os.path.join(patcher_dir, CONFIG_FILE_NAME)
    
    config_file_path_parent = os.path.join(os.path.dirname(patcher_dir), CONFIG_FILE_NAME)

    # from the prev versions
    paths_to_try = [
        config_file_path_local,
        os.path.join(patcher_dir, "..", CONFIG_FILE_NAME) # If patcher is in a subfolder of custom_nodes
    ]
    if patcher_dir.endswith("custom_nodes"): # If patcher is directly in custom_nodes
        paths_to_try = [os.path.join(patcher_dir, CONFIG_FILE_NAME)]
    else: # Patcher is likely in custom_nodes/some_folder/
        paths_to_try = [
            os.path.join(patcher_dir, CONFIG_FILE_NAME),
            os.path.join(os.path.dirname(patcher_dir), CONFIG_FILE_NAME)
        ]


    loaded_path = None
    for path_attempt in paths_to_try:
        if os.path.exists(path_attempt):
            config_file_path = path_attempt
            loaded_path = config_file_path
            break
    else:
        logger.info(f"Configuration file '{CONFIG_FILE_NAME}' not found in expected locations. Using default settings.")
        # Configure logger with default level before returning
        log_level_str = DEFAULT_CONFIG.get("patcher_log_level", "INFO").upper()
        logger.setLevel(getattr(logging, log_level_str, logging.INFO))
        if not logger.hasHandlers(): # Add a handler if none exist (e.g. during early startup)
            handler = logging.StreamHandler(sys.stderr) # Log to stderr
            formatter = logging.Formatter(f"[{PATCHER_NAME}] [%(levelname)s] %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return

    try:
        with open(config_file_path, 'r') as f:
            user_config = json.load(f)
        config.update(user_config)
        #logger.info(f"Successfully loaded configuration from '{config_file_path}'.")
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from '{config_file_path}': {e}. Using default settings.")
    except Exception as e:
        logger.error(f"Error loading configuration from '{config_file_path}': {e}. Using default settings.")

    log_level_str = config.get("patcher_log_level", "INFO").upper()
    logger.setLevel(getattr(logging, log_level_str, logging.INFO))
    if not logger.hasHandlers():
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(f"[{PATCHER_NAME}] [%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Override to DEBUG if debug mode is on
    if config.get("patcher_debug_mode"):
        logger.setLevel(logging.DEBUG) 
        logger.debug("Patcher debug mode enabled.")
    
    logger.debug(f"Current patcher configuration: {config}")


def find_comfy_nodes_module():
    """
    Attempts to find ComfyUI's main 'nodes' module in sys.modules.
    """
    global comfy_nodes_module
    potential_names = ['nodes', 'comfy.nodes'] 
    for name in potential_names:
        if name in sys.modules and hasattr(sys.modules[name], 'load_custom_node') and hasattr(sys.modules[name], 'get_module_name'):
            logger.debug(f"Found ComfyUI nodes module as '{name}'.")
            return sys.modules[name]

    for mod_name, mod_obj in sys.modules.items():
        if hasattr(mod_obj, 'load_custom_node') and \
           hasattr(mod_obj, 'get_module_name') and \
           hasattr(mod_obj, 'init_external_custom_nodes'):
            logger.debug(f"Found ComfyUI nodes module by searching as '{mod_name}'.")
            return mod_obj
    return None

def patched_load_custom_node(module_path: str, ignore=set(), module_parent="custom_nodes") -> bool:
    """
    wrapper around the original load_custom_node.
    It will silence stdout for specified modules based on loaded config.
    """
    global original_load_custom_node, original_get_module_name, config

    if not original_load_custom_node or not original_get_module_name:
        logger.error("Original functions not available for patched_load_custom_node. Bypassing patch.")
        if comfy_nodes_module and hasattr(comfy_nodes_module, 'load_custom_node'):
             return comfy_nodes_module.load_custom_node(module_path, ignore, module_parent)
        return False

    module_name_for_check = original_get_module_name(module_path)
    
    modules_to_silence_set = set(config.get("modules_to_silence", []))
    should_silence = module_name_for_check in modules_to_silence_set
    
    current_stdout = None
    temp_stdout_buffer = None

    if should_silence:
        logger.info(f"Silencing stdout for module: {module_name_for_check} (Path: {module_path})")
        current_stdout = sys.stdout
        temp_stdout_buffer = io.StringIO()
        sys.stdout = temp_stdout_buffer
    
    try:
        result = original_load_custom_node(module_path, ignore, module_parent)
        if should_silence and temp_stdout_buffer and config.get("log_suppressed_output"):
            output = temp_stdout_buffer.getvalue()
            if output:
                 logger.debug(f"Suppressed output from {module_name_for_check}:\n------START SUPPRESSED OUTPUT------\n{output.strip()}\n-------END SUPPRESSED OUTPUT-------")
        return result
    except Exception as e:
        logger.error(f"Error during original load_custom_node for {module_path}: {e}", exc_info=True)
        raise
    finally:
        if should_silence and current_stdout is not None:
            sys.stdout = current_stdout
            if temp_stdout_buffer:
                temp_stdout_buffer.close()
            logger.info(f"Restored stdout after module: {module_name_for_check}")

def patch_comfy_loader():
    global comfy_nodes_module, original_load_custom_node, original_get_module_name

    load_patcher_config() 

    logger.info("Attempting to patch ComfyUI custom node loader...")

    comfy_nodes_module = find_comfy_nodes_module()

    if not comfy_nodes_module:
        logger.critical("Could not find ComfyUI's main nodes module. Patching aborted.")
        return False

    if not hasattr(comfy_nodes_module, 'load_custom_node') or not hasattr(comfy_nodes_module, 'get_module_name'):
        logger.critical(f"Target functions not found in module '{comfy_nodes_module.__name__}'. Patching aborted.")
        return False

    original_load_custom_node = comfy_nodes_module.load_custom_node
    original_get_module_name = comfy_nodes_module.get_module_name

    if hasattr(original_load_custom_node, '_is_patched_by_000_startup_patcher'):
        logger.info("ComfyUI loader already patched by this script. Skipping.")
        return True

    comfy_nodes_module.load_custom_node = patched_load_custom_node
    setattr(patched_load_custom_node, '_is_patched_by_000_startup_patcher', True) # Mark our wrapper
    
    logger.info(f"Successfully patched 'load_custom_node' in module '{comfy_nodes_module.__name__}'.")
    logger.info(f"Modules configured to be silenced: {config.get('modules_to_silence')}")
    return True

# Actual patching
try:
    _temp_handler = logging.StreamHandler(sys.stderr)
    _temp_formatter = logging.Formatter(f"[{PATCHER_NAME}] [%(levelname)s] %(message)s")
    _temp_handler.setFormatter(_temp_formatter)
    logger.addHandler(_temp_handler)
    logger.setLevel(logging.INFO) 

    if patch_comfy_loader():
        if os.environ.get(ENV_VAR_NAME) == ENV_VAR_VALUE:
            logger.info(f"Environment variable '{ENV_VAR_NAME}' is active with value '{ENV_VAR_VALUE}'.")
        else:
            logger.warning(f"Environment variable '{ENV_VAR_NAME}' not found or value mismatch after startup. Current: '{os.environ.get(ENV_VAR_NAME)}'")
        logger.info("Patcher initialization complete.")
    else:
        logger.error("Patcher initialization failed.")
    
    # Remove temporary handler if full logging was set up by load_patcher_config
    # Check if more permanent handlers were added by load_patcher_config
    if len(logger.handlers) > 1 and logger.handlers[0] == _temp_handler:
        logger.removeHandler(_temp_handler)
    elif len(logger.handlers) == 1 and logger.handlers[0] == _temp_handler and not os.path.exists(os.path.join(os.path.dirname(os.path.realpath(__file__)), CONFIG_FILE_NAME)):
        pass


except Exception as e:
    logger.critical(f"FATAL ERROR during patcher loading or patching process: {e}", exc_info=True)
    ## Also print to raw stderr for maximum visibility of critical errors
    # traceback.print_exc() 

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
# no web yet
WEB_DIRECTORY = False 

logger.debug(f"'{PATCHER_NAME}' module execution finished.")