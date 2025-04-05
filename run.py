import os
import sys
import subprocess
import platform

# --- Configuration ---
VENV_DIR = ".venv"
MAIN_SCRIPT = "monitor_youtube.py"
# --- End Configuration ---

def get_venv_python_executable():
    """Gets the path to the python executable inside the venv."""
    base_dir = os.path.dirname(os.path.abspath(__file__)) # Directory where run.py is
    venv_path = os.path.join(base_dir, VENV_DIR)

    if not os.path.isdir(venv_path):
        print(f"ERROR: Virtual environment directory '{VENV_DIR}' not found in {base_dir}.")
        print(f"Please run 'python setup.py' first.")
        sys.exit(1)

    if platform.system() == "Windows":
        python_exe = os.path.join(venv_path, "Scripts", "python.exe")
    else: # Linux, macOS
        python_exe = os.path.join(venv_path, "bin", "python")
        if not os.path.exists(python_exe):
             # Handle systems where venv python is python3
             python_exe = os.path.join(venv_path, "bin", "python3")


    if not os.path.exists(python_exe):
        print(f"ERROR: Python executable not found in virtual environment: {python_exe}")
        print(f"The virtual environment might be corrupted or incomplete. Try running 'python setup.py' again.")
        sys.exit(1)

    return python_exe

def check_main_script():
     base_dir = os.path.dirname(os.path.abspath(__file__))
     script_path = os.path.join(base_dir, MAIN_SCRIPT)
     if not os.path.exists(script_path):
          print(f"ERROR: Main script '{MAIN_SCRIPT}' not found in {base_dir}.")
          sys.exit(1)
     return script_path


if __name__ == "__main__":
    print(f"--- Wrapper: Activating venv and running {MAIN_SCRIPT} ---")
    venv_python = get_venv_python_executable()
    main_script_path = check_main_script()
    script_dir = os.path.dirname(main_script_path) # Get dir of monitor_youtube.py

    print(f"Using Python from venv: {venv_python}")
    print(f"Executing script: {main_script_path}")
    print(f"Working directory: {script_dir}") # Log the cwd being used

    try:
        # Run the main script using the python from the virtual environment
        # Set the cwd so monitor_youtube.py finds config.ini etc., relative to itself.
        process = subprocess.run(
            [venv_python, main_script_path],
            check=True,
            cwd=script_dir, # Set working directory to where monitor_youtube.py is
            encoding='utf-8',
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE
        )

        if process.stdout:
             print("\n--- Script Output ---")
             print(process.stdout)
        if process.stderr:
             print("\n--- Script Error Output ---", file=sys.stderr)
             print(process.stderr, file=sys.stderr)

        print(f"--- Wrapper: {MAIN_SCRIPT} finished successfully ---")
        sys.exit(0)

    except FileNotFoundError:
         print(f"ERROR: Could not execute Python from venv '{venv_python}'. Check venv integrity.")
         sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: The main script '{MAIN_SCRIPT}' exited with an error (code {e.returncode}).")
        if e.stdout:
             print("\n--- Script Output ---")
             print(e.stdout)
        if e.stderr:
             print("\n--- Script Error Output ---", file=sys.stderr)
             print(e.stderr, file=sys.stderr)
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        print("\nExecution interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: An unexpected error occurred while running the script: {e}")
        sys.exit(1)