#!/bin/bash

# install Homebrew
# /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"

# install zsh
# brew install zsh

# install omz
#sh -c "$(curl -fsSL https://raw.githubusercontent.com/robbyrussell/oh-my-zsh/master/tools/install.sh)"

# install tmux fonts
git clone https://github.com/powerline/fonts.git --depth=1
cd fonts
./install.sh
cd ..
rm -rf fonts

# install tmux and pre-reqs
brew install tmux
brew install reattach-to-user-namespace
git clone https://github.com/gpakosz/.tmux.git $HOME/.tmux
ln -s -f $HOME/.tmux/.tmux.conf $HOME/.
ln -sf $(PWD)/.tmux.conf.local $HOME/.

# install other important brew stuff
brew install terraform azure-cli python jq node vim jq yarn dtrx kubernetes-cli kubernetes-helm

cat << 'EOF' >> ~/.zshrc
export LANG=en_US.UTF-8
alias nuke_docker='docker rm --force $(docker ps -a -q)'
export HOMEBREW_CELLAR="/usr/local/Cellar"
autoload -U +X bashcompinit && bashcompinit
export EDITOR='vim'
export SSH_KEY_PATH="~/.ssh/rsa_id"
export GPG_TTY=`tty`
bindkey '^R' history-incremental-search-backward
source <(helm completion zsh)
source <(kubectl completion zsh)
source /usr/local/etc/bash_completion.d/az
EOF

# add zsh-syntax-highlighting plugin to omz plugins
git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting

cd ~/.oh-my-zsh/custom/plugins && git clone https://github.com/sbodiu-pivotal/fly-zsh-autocomplete-plugin.git fly



# add default plugins to .zshrc
for PLUGIN in ['fly', 'docker', 'kubectl', 'zsh-syntax-highlighting']; do
    sed '/plugins=(/a\'$'\n' ${PLUGIN} ~/.zshrc
done

source ~/.zshrc
