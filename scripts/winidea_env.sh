# Source this file after installation if you want the same shell configured:
#   source scripts/winidea_env.sh

export WINIDEA_HOME="${WINIDEA_HOME:-$HOME/.local/opt/winidea}"
export WINIDEA_EXE_DIR="${WINIDEA_EXE_DIR:-$WINIDEA_HOME}"
export DAS_HOME="${DAS_HOME:-/opt/Tools/DAS/8.3.0}"
export TAS_HOME="${TAS_HOME:-$DAS_HOME}"

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac

case ":$PATH:" in
  *":$WINIDEA_HOME:"*) ;;
  *) export PATH="$WINIDEA_HOME:$PATH" ;;
esac

case ":$PATH:" in
  *":$DAS_HOME/bin:"*) ;;
  *) export PATH="$DAS_HOME/bin:$PATH" ;;
esac
