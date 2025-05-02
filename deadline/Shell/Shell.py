#!/usr/bin/env python3
from __future__ import absolute_import

from Deadline.Plugins import *
from Deadline.Scripting import *

import random
import string
import os

def GetDeadlinePlugin():
    return ShellPlugin()

def CleanupDeadlinePlugin(deadlinePlugin):
    deadlinePlugin.Cleanup()

def _to_wsl_path(path):
    raw_path = path.replace('\\', '/')
    if raw_path.startswith('/mnt/'): return path
    parts = raw_path.split('/')
    drive = parts[0][:-1].lower()
    return '/'.join(['', 'mnt', drive] + parts[1:])

def _to_windows_path(path):
    raw_path = path.replace('\\', '/')
    if not raw_path.startswith('/mnt/'): return path
    parts = raw_path.split('/')
    drive = f'{parts[2].upper()}:'
    return '/'.join([drive] + parts[3:])

def _random_env_name():
    return ''.join(random.choices(string.hexdigits, k=16))

class ShellPlugin(DeadlinePlugin):
    def __init__(self):
        super().__init__()

        # Members
        self._env_name = _random_env_name()

        # Callbacks
        self.InitializeProcessCallback += self._initialize_process
        self.RenderExecutableCallback += self._render_executable
        self.RenderArgumentCallback += self._render_argument
        self.PreRenderTasksCallback += self._create_python_environment
        self.CheckExitCodeCallback += self._remove_python_environment
    
    def Cleanup(self):
        del self.InitializeProcessCallback
        del self.RenderExecutableCallback
        del self.RenderArgumentCallback
        del self.PreRenderTasksCallback
        del self.PostRenderTasksCallback
    
    def _initialize_process(self):

        # Settings
        self.SingleFramesOnly = self.GetBooleanPluginInfoEntryWithDefault('SingleFramesOnly', False)
        self.PluginType = PluginType.Simple

        self.UseProcessTree = True
        self.StdoutHandling = True

        # Set up stdout handlers
        self.AddStdoutHandlerCallback('.*Progress: (\d+)%.*').HandleCallback += self._handle_progress

    def get_env(self):
        return self._env_name

    def get_cwd(self):
        cwd_path = self.GetPluginInfoEntryWithDefault('StartupDirectory', '')
        if SystemUtils.IsRunningOnWindows(): return _to_windows_path(cwd_path)
        return _to_wsl_path(cwd_path)
    
    def get_python_version(self):
        return self.GetPluginInfoEntryWithDefault('PythonVersion', '3.11')

    def get_script_path(self):
        script_path = self.GetPluginInfoEntryWithDefault('ScriptFile', '')
        if SystemUtils.IsRunningOnWindows(): return _to_windows_path(script_path)
        return _to_wsl_path(script_path)
    
    def get_environment_path(self):
        env_path = self.GetPluginInfoEntryWithDefault('EnvironmentFile', '')
        if SystemUtils.IsRunningOnWindows(): return _to_windows_path(env_path)
        return _to_wsl_path(env_path)
    
    def get_requirements_path(self):
        req_path = self.GetPluginInfoEntryWithDefault('RequirementsFile', '')
        if SystemUtils.IsRunningOnWindows(): return _to_windows_path(req_path)
        return _to_wsl_path(req_path)

    def get_arguments(self):
        return self.GetPluginInfoEntryWithDefault('Arguments', '')
    
    def _run_windows(self, command, cwd_path):
        command = ['--shell-type', 'login'] + command
        arguments = ' '.join(filter(lambda part: len(part) != 0, command))
        return self.RunProcess('C:/Windows/System32/wsl.exe', arguments, cwd_path, -1) == 0
        
    def _run_linux(self, command, cwd_path):
        arguments = ' '.join(filter(lambda part: len(part) != 0, command))
        return self.RunProcess('/usr/bin/bash', arguments, cwd_path, -1) == 0

    def _run(self, command, cwd_path):
        if SystemUtils.IsRunningOnWindows():
            return self._run_windows(command, cwd_path)
        return self._run_linux(command, cwd_path)

    def _create_python_environment(self):

        # Parameters
        cwd_path = self.get_cwd()
        env_name = self.get_env()

        # Create a python environment
        python_version = self.get_python_version()
        self.LogInfo(f' Creating python {python_version} environment '.center(100, '='))
        success = self._run(['micromamba', 'env', 'create', '-n', env_name, f'python={python_version}', '-c', 'conda-forge', '-y'], cwd_path)
        if not success: return self.FailRender('Failed to create python environment')

        # Install plugin require packages
        self.LogInfo(' Installing requirements '.center(100, '='))
        success = self._run(['micromamba', 'install', '-n', env_name, 'python-dotenv', '-c', 'conda-forge', '-y'], cwd_path)
        if not success: return self.FailRender('Failed to install plugin requirements')

        # Install the required packages
        req_file_path = self.get_requirements_path()
        if req_file_path != '':
            success = self._run(['micromamba', 'run', '-n', env_name, 'pip', 'install', '--upgrade', '-r', _to_wsl_path(req_file_path)], cwd_path)
            if not success: return self.FailRender('Failed to install job requirements')
    
    def _render_executable(self):
        if SystemUtils.IsRunningOnWindows():
            return 'C:/Windows/System32/wsl.exe'
        return '/usr/bin/bash'
    
    def _runner_script_path(self):
        return os.path.join(os.path.dirname(__file__), 'Runner.py')
    
    def _runner_command(self, script_path, arguments):
        command = [
            'micromamba', 'run', '-n', self.get_env(),
            'python', _to_wsl_path(self._runner_script_path())
        ]
        env_path = self.get_environment_path()
        if env_path != '': command += ['--env', _to_wsl_path(env_path)]
        command += ['--cwd', _to_wsl_path(self.get_cwd())]
        command += [_to_wsl_path(script_path), *arguments]
        return command

    def _render_argument(self):
        
        # Parameters and paths
        env_name = self.get_env()
        cwd_path = self.get_cwd()
        script_path = self.get_script_path()
        arguments = self.get_arguments()
        start_frame = self.GetStartFrame()
        end_frame = self.GetEndFrame()

        # Report settings
        self.LogInfo(' Running task '.center(100, '='))
        self.LogInfo(f'Environment name: {env_name}')
        self.LogInfo(f'CWD path found: {cwd_path}')
        self.LogInfo(f'Script found: {script_path}')
        self.LogInfo(f'Arguments found: {arguments}')
        self.LogInfo(f'Frame range: {start_frame}-{end_frame}')

        # Run the script in the python environment
        return ' '.join(
            (['--shell-type', 'login'] if SystemUtils.IsRunningOnWindows() else []) +
            self._runner_command(script_path, arguments.split(' ') + [str(start_frame), str(end_frame)])
        )
    
    def _remove_python_environment(self, return_code):

        # Parameters
        env_name = self.get_env()
        cwd_path = self.get_cwd()

        # Remove the python environment
        self.LogInfo(' Removing python environment '.center(100, '='))
        success = self._run(['micromamba', 'env', 'remove', '-n', env_name, '-y'], cwd_path)
        if not success: return self.FailRender('Failed to remove python environment')

        # Handle the return code
        if return_code == 0: return
        self.FailRender(f'Failed with return code: {return_code}')
    
    def _handle_progress(self):
        progress = float(self.GetRegexMatch(1))
        self.SetProgress(progress)