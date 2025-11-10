@echo off
TITLE "Turbulence Houdini 21.0"
SET TH_USER=tumblehead
SET TH_PROJECT_PATH=P:/Turbulence
SET TH_PIPELINE_PATH=P:/TumblePipe
SET TH_CONFIG_PATH=%TH_PROJECT_PATH%/_config
SET OCIO=%TH_PIPELINE_PATH%/ocio/tumblehead.ocio
SET HOUDINI_PACKAGE_DIR=%TH_PIPELINE_PATH%/houdini;%TH_PROJECT_PATH%/_pipeline/houdini
"C:/Program Files/Side Effects Software/Houdini 21.0.512/bin/houdini.exe"
exit