# Path to your oh-my-zsh installation.
export ZSH=/Users/athomas/.oh-my-zsh

# Set name of the theme to load.
# Look in ~/.oh-my-zsh/themes/
# Optionally, if you set this to "random", it'll load a random theme each
# time that oh-my-zsh is loaded.
ZSH_THEME="robbyrussell"

# Uncomment the following line to use case-sensitive completion.
# CASE_SENSITIVE="true"

# Uncomment the following line to use hyphen-insensitive completion. Case
# sensitive completion must be off. _ and - will be interchangeable.
# HYPHEN_INSENSITIVE="true"

# Uncomment the following line to disable bi-weekly auto-update checks.
# DISABLE_AUTO_UPDATE="true"

# Uncomment the following line to change how often to auto-update (in days).
# export UPDATE_ZSH_DAYS=13

# Uncomment the following line to disable colors in ls.
# DISABLE_LS_COLORS="true"

# Uncomment the following line to disable auto-setting terminal title.
# DISABLE_AUTO_TITLE="true"

# Uncomment the following line to enable command auto-correction.
# ENABLE_CORRECTION="true"

# Uncomment the following line to display red dots whilst waiting for completion.
# COMPLETION_WAITING_DOTS="true"

# Uncomment the following line if you want to disable marking untracked files
# under VCS as dirty. This makes repository status check for large repositories
# much, much faster.
# DISABLE_UNTRACKED_FILES_DIRTY="true"

# Uncomment the following line if you want to change the command execution time
# stamp shown in the history command output.
# The optional three formats: "mm/dd/yyyy"|"dd.mm.yyyy"|"yyyy-mm-dd"
# HIST_STAMPS="mm/dd/yyyy"

# Would you like to use another custom folder than $ZSH/custom?
# ZSH_CUSTOM=/path/to/new-custom-folder

# Which plugins would you like to load? (plugins can be found in ~/.oh-my-zsh/plugins/*)
# Custom plugins may be added to ~/.oh-my-zsh/custom/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(git zsh-syntax-highlighting pass fuck task ssh-agent vi-mode virtualenvwrapper osx )

# User configuration

export PATH="/Users/athomas/homebrew/bin:/Users/athomas/tools/jmeter/apache-jmeter-2.13/bin:/Users/athomas/homebrew/bin:/Users/athomas/tools/jmeter/apache-jmeter-2.13/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/X11/bin:/usr/local/munki:/Users/athomas:/Users/athomas:/Users/athomas/homebrew/Cellar/gettext/0.19.7/bin/"
# export MANPATH="/usr/local/man:$MANPATH"

source $ZSH/oh-my-zsh.sh

# You may need to manually set your language environment
# export LANG=en_US.UTF-8

# Preferred editor for local and remote sessions
# if [[ -n $SSH_CONNECTION ]]; then
#   export EDITOR='vim'
# else
#   export EDITOR='mvim'
# fi

# Compilation flags
# export ARCHFLAGS="-arch x86_64"

# ssh
# export SSH_KEY_PATH="~/.ssh/dsa_id"

# Set personal aliases, overriding those provided by oh-my-zsh libs,
# plugins, and themes. Aliases can be placed here, though oh-my-zsh
# users are encouraged to define aliases within the ZSH_CUSTOM folder.
# For a full list of active aliases, run `alias`.
#
# Example aliases
# alias zshconfig="mate ~/.zshrc"
# alias ohmyzsh="mate ~/.oh-my-zsh"
export IMAGEORG=narsil.ad.pdrop.net:5000
alias nukeit='docker rm --force $(docker ps -a -q)'
alias nuke_cleaner="docker rm -f cleaner mysqlcleaner s3"
bindkey -M viins ‘jj’ vi-cmd-mode
source ~/perl5/perlbrew/etc/bashrc
export PELRBREW_CPAN_MIRROR=http://mirror.transip.net/CPAN/
alias upgrade_nuke='docker rm -f $(docker ps -a | grep -v mysql)'
# alias envsubst='/Users/athomas/homebrew/Cellar/gettext/0.19.7/bin/envsubst'
alias clean_pycache="find . -type f -name '*.pyc' -delete"
alias kill_all_vagrants="vagrant global-status | grep virtualbox | awk '{print $1}' | xargs -n1 vagrant destroy -f"
alias eval_docker='eval "$(docker-machine env default)"'
