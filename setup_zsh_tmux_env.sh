#!/bin/bash

# install Homebrew (if mac)
# /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"

# install zsh
# mac:
# brew install zsh
# ubuntu
# apt-get install zsh

# install omz
#sh -c "$(curl -fsSL https://raw.githubusercontent.com/robbyrussell/oh-my-zsh/master/tools/install.sh)"

# install tmux fonts
git clone https://github.com/powerline/fonts.git --depth=1
cd fonts
./install.sh
cd ..
rm -rf fonts

if [[ "$OSTYPE" == "darwin"* ]]; then
    # install tmux and pre-reqs
    brew update && brew install tmux \
        reattach-to-user-namespace \
        python3 \
        vim \
        jq \
        dtrx
elif [[ "$OSTYPE" == "linux-gnu" ]]; then
    # TODO: add command -b switch to add yum and pacman support
    apt-get install -y tmux \
        xclip \
        python3 \
        vim \
        jq \
        dtrx
fi
git clone https://github.com/gpakosz/.tmux.git $HOME/.tmux
ln -s -f $HOME/.tmux/.tmux.conf $HOME/.
cp $HOME/.tmux/.tmux.conf.local $HOME/.

sed -i .bak -e 's/tmux_conf_theme_left_separator/#tmux_conf_theme_left_separator/g' \
    -e 's/tmux_conf_theme_right_separator/#tmux_conf_theme_right_separator/g' \
    -e "s/#tmux_conf_theme_left_separator_main=''/tmux_conf_theme_left_separator_main=''/" \
    -e "s/#tmux_conf_theme_left_separator_sub=''/tmux_conf_theme_left_separator_sub=''/" \
    -e "s/#tmux_conf_theme_right_separator_main=''/tmux_conf_theme_right_separator_main=''/" \
    -e "s/#tmux_conf_theme_right_separator_sub=''/tmux_conf_theme_right_separator_sub=''/" \
    -e 's/tmux_conf_copy_to_os_clipboard=false/tmux_conf_copy_to_os_clipboard=true/' \
    -e 's/#set -g mouse on/set -g mouse on/' \
    -e 's/# set -gu prefix2/set -gu prefix2/' \
    -e 's/# unbind C-a/unbind C-a/' \
    -e 's/# unbind C-b/unbind C-b/' \
    -e 's/# set -g prefix C-a/set -g prefix C-a/' \
    -e 's/# bind C-a send-prefix/bind C-a send-prefix/' \
    .tmux.conf.local

cat << 'EOF' >> ~/.tmux.conf.local
bind a last-window
bind n next-window
EOF



cat << 'EOF' >> ~/.zshrc
export LANG=en_US.UTF-8
alias nuke_docker='docker rm --force $(docker ps -a -q)'
#export HOMEBREW_CELLAR="/usr/local/Cellar"
#autoload -U +X bashcompinit && bashcompinit
export EDITOR='vim'
export SSH_KEY_PATH="~/.ssh/rsa_id"
export GPG_TTY=`tty`
bindkey '^R' history-incremental-search-backward
#source <(helm completion zsh)
#source <(kubectl completion zsh)
#source /usr/local/etc/bash_completion.d/az
EOF

# add zsh-syntax-highlighting plugin to omz plugins
git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting

# add default plugins to .zshrc
for PLUGIN in ['zsh-syntax-highlighting']; do
    sed '/plugins=(/a\'$'\n' ${PLUGIN} ~/.zshrc
done

ln -s -f scripts/clean_docker_cache /usr/local/bin/.
ln -s -f scripts/clean_python_cache /usr/local/bin/.

git clone https://github.com/AXington/.vim.git $HOME/.vim
current_dir=$(pwd)
cd $HOME/.vim && git checkout heavenly && cd $current_dir

source ~/.zshrc
