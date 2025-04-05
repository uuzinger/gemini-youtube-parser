import os
import sys
import subprocess
import venv
import platform

print("--- YouTube Monitor Setup Script ---")

# --- Configuration ---
VENV_DIR = ".venv"
REQUIREMENTS_FILE = "requirements.txt"
OUTPUT_DIR_NAME = "output" # Defined in config.ini, but needed here too
CONFIG_FILE_NAME = "config.ini"
MAIN_SCRIPT_NAME = "monitor_youtube.py"
RUN_SCRIPT_NAME = "run.py"
# --- End Configuration ---

# --- Helper Functions ---
def check_python():
    """Checks for Python 3.7+"""
    print("Checking Python version...")
    if sys.version_info < (3, 7):
        print(f"ERROR: Python 3.7 or higher is required. You have {sys.version}.")
        sys.exit(1)
    print(f"Python version {sys.version} found. OK.")
    return sys.executable # Return path to current python executable

def check_pip(python_executable):
    """Checks if pip is available."""
    print("Checking pip availability...")
    try:
        subprocess.run([python_executable, "-m", "pip", "--version"], check=True, capture_output=True)
        print("pip is available. OK.")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: pip is not available for the current Python interpreter.")
        print("Please ensure pip is installed (e.g., 'python -m ensurepip --upgrade' or install via package manager).")
        sys.exit(1)

def create_virtual_env():
    """Creates a virtual environment if it doesn't exist."""
    if os.path.exists(VENV_DIR):
        print(f"Virtual environment '{VENV_DIR}' already exists. Skipping creation.")
    else:
        print(f"Creating virtual environment in '{VENV_DIR}'...")
        try:
            venv.create(VENV_DIR, with_pip=True)
            print("Virtual environment created successfully.")
        except Exception as e:
            print(f"ERROR: Failed to create virtual environment: {e}")
            sys.exit(1)

def get_venv_executable(executable_name):
    """Gets the path to an executable (like python or pip) inside the venv."""
    if platform.system() == "Windows":
        path = os.path.join(VENV_DIR, "Scripts", f"{executable_name}.exe")
    else: # Linux, macOS
        path = os.path.join(VENV_DIR, "bin", executable_name)

    if not os.path.exists(path):
         # Fallback for some Linux distributions or configurations
         if platform.system() != "Windows" and executable_name == "python":
             path = os.path.join(VENV_DIR, "bin", "python3")
             if not os.path.exists(path):
                 print(f"ERROR: Could not find '{executable_name}' executable in virtual environment at expected location.")
                 sys.exit(1)
         else:
            print(f"ERROR: Could not find '{executable_name}' executable in virtual environment at expected location: {path}")
            sys.exit(1)
    return path

def install_requirements(venv_python_executable):
    """Installs packages from requirements.txt using the venv pip."""
    venv_pip_executable = get_venv_executable("pip")
    print(f"Installing dependencies from {REQUIREMENTS_FILE} using {venv_pip_executable}...")
    try:
        # Upgrade pip first within the venv
        subprocess.run([venv_python_executable, "-m", "pip", "install", "--upgrade", "pip"], check=True, capture_output=True)
        # Install requirements
        subprocess.run([venv_python_executable, "-m", "pip", "install", "-r", REQUIREMENTS_FILE], check=True, capture_output=False) # show output
        print("Dependencies installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to install dependencies.")
        print(f"Command failed: {' '.join(e.cmd)}")
        print(f"Output:\n{e.output.decode() if e.output else 'N/A'}")
        print(f"Stderr:\n{e.stderr.decode() if e.stderr else 'N/A'}")
        sys.exit(1)
    except FileNotFoundError:
         print(f"ERROR: Could not execute pip using '{venv_pip_executable}'. Ensure the virtual environment is correctly set up.")
         sys.exit(1)
    except Exception as e:
        print(f"ERROR: An unexpected error occurred during dependency installation: {e}")
        sys.exit(1)

def create_output_directory():
    """Creates the output directory if it doesn't exist."""
    if not os.path.exists(OUTPUT_DIR_NAME):
        print(f"Creating output directory '{OUTPUT_DIR_NAME}'...")
        try:
            os.makedirs(OUTPUT_DIR_NAME)
            print("Output directory created.")
        except OSError as e:
            print(f"ERROR: Could not create output directory '{OUTPUT_DIR_NAME}': {e}")
            sys.exit(1)
    else:
        print(f"Output directory '{OUTPUT_DIR_NAME}' already exists.")

def check_required_files():
    """Checks if essential files exist."""
    print("Checking for required files...")
    files_to_check = [REQUIREMENTS_FILE, CONFIG_FILE_NAME, MAIN_SCRIPT_NAME, RUN_SCRIPT_NAME]
    all_found = True
    for filename in files_to_check:
        if not os.path.exists(filename):
            print(f"ERROR: Required file '{filename}' not found in the current directory.")
            all_found = False
    if not all_found:
        print("Please ensure all required files are present before running setup.")
        sys.exit(1)
    print("All required files found. OK.")


# --- Main Setup Logic ---
if __name__ == "__main__":
    python_exe = check_python()
    check_pip(python_exe)
    check_required_files() # Check before creating things
    create_virtual_env()
    venv_python_exe = get_venv_executable("python")
    install_requirements(venv_python_exe)
    create_output_directory()

    print("\n--- Setup Complete! ---")
    if not os.path.exists(CONFIG_FILE_NAME) or os.path.getsize(CONFIG_FILE_NAME) < 100: # Basic check if config might be default
         print(f"IMPORTANT: Please edit the '{CONFIG_FILE_NAME}' file with your API keys, channel IDs, and email settings.")
    else:
         print(f"Reminder: Ensure your API keys, channel IDs, and email settings in '{CONFIG_FILE_NAME}' are correct.")

    print(f"\nTo run the monitor manually, execute:")
    print(f"  python {RUN_SCRIPT_NAME}")
    print(f"\nTo schedule the script:")
    print(f"  - Linux/macOS: Use cron to run 'python3 /path/to/your/project/{RUN_SCRIPT_NAME}' (use absolute paths)")
    print(f"  - Windows: Use Task Scheduler to run 'python C:\\path\\to\\your\\project\\{RUN_SCRIPT_NAME}'")
    print("    (Ensure you set the 'Start in' directory correctly in the scheduler task to your project's absolute path)")

    sys.exit(0)