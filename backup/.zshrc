# If you come from bash you might have to change your $PATH.
# export PATH=$HOME/bin:/usr/local/bin:$PATH

# Path to your oh-my-zsh installation.
export ZSH="/Users/h272584/.oh-my-zsh"

# Set name of the theme to load. Optionally, if you set this to "random"
# it'll load a random theme each time that oh-my-zsh is loaded.
# See https://github.com/robbyrussell/oh-my-zsh/wiki/Themes
ZSH_THEME="agnoster"

export VIRTUALENVWRAPPER_PYTHON="/usr/local/bin/python3"

# Which plugins would you like to load? (plugins can be found in ~/.oh-my-zsh/plugins/*)
# Custom plugins may be added to ~/.oh-my-zsh/custom/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(
  fly
  docker
  python
  kubectl
  virtualenvwrapper
  zsh-syntax-highlighting
)

source $ZSH/oh-my-zsh.sh

# User configuration


# Preferred editor for local and remote sessions
export EDITOR='vim'

# Compilation flags
# export ARCHFLAGS="-arch x86_64"

# ssh
export SSH_KEY_PATH="~/.ssh/rsa_id"

# Set personal aliases, overriding those provided by oh-my-zsh libs,
# plugins, and themes. Aliases can be placed here, though oh-my-zsh
# users are encouraged to define aliases within the ZSH_CUSTOM folder.
# For a full list of active aliases, run `alias`.
#
# Example aliases
# alias zshconfig="mate ~/.zshrc"
# alias ohmyzsh="mate ~/.oh-my-zsh"

alias nuke_docker='docker rm --force $(docker ps -a -q)'
prompt_context(){}
export HOMEBREW_CELLAR="/usr/local/Cellar"
#alias setJdk6='export JAVA_HOME=$(/usr/libexec/java_home -v 1.6)'
#alias setJdk7='export JAVA_HOME=$(/usr/libexec/java_home -v 1.7)'
alias setJdk8='export JAVA_HOME=$(/usr/libexec/java_home -v 1.8)'
#alias setJdk9='export JAVA_HOME=$(/usr/libexec/java_home -v 9.0)'
export JAVA_HOME=$(/usr/libexec/java_home -v 1.8)
export GPG_TTY=`tty`
bindkey '^R' history-incremental-search-backward
source ~/.vault_auth || true
export LANG=en_US.UTF-8

autoload -U +X bashcompinit && bashcompinit
source /usr/local/etc/bash_completion.d/az
complete -o nospace -C /usr/local/bin/vault vault

source <(helm completion zsh)
source <(kubectl completion zsh)
source <(npm completion)

#GOLang
export GOPATH="${HOME}/.go"
export GOROOT="$(brew --prefix golang)/libexec"
export PATH="$PATH:${GOPATH}/bin:${GOROOT}/bin"
test -d "${GOPATH}" || mkdir "${GOPATH}"
test -d "${GOPATH}/src/github.com" || mkdir -p "${GOPATH}/src/github.com"

WORKON_HOME=$HOME/.virtualenvs

tmux attach || tmux new
