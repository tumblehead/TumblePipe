@echo off
TITLE "Turbulence Houdini 20.5"
SET TH_USER=tumblehead
SET TH_PROJECT_PATH=P:/Turbulence
SET TH_PIPELINE_PATH=P:/TumblePipe
SET TH_CONFIG_PATH=%TH_PROJECT_PATH%/_config
SET HOUDINI_PACKAGE_DIR=%TH_PIPELINE_PATH%/houdini;%TH_PROJECT_PATH%/_pipeline/houdini
"C:/Program Files/Side Effects Software/Houdini 20.5.550/bin/houdinifx.exe"
exit