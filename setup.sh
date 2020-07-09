#!/usr/bin/env bash

# install Homebrew (if mac)
# /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"

# install zsh
# mac:
# brew install zsh
# ubuntu
# apt-get install zsh

# install omz
#sh -c "$(curl -fsSL https://raw.githubusercontent.com/robbyrussell/oh-my-zsh/master/tools/install.sh)"


if [[ "$OSTYPE" == "darwin"* ]]; then
    # install tmux and pre-reqs
    /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"

    brew update

    cat brew_packages.txt | xargs -I{} brew install {}

    # symlink all the gnu binaries to ~/.gnubin
    mkdir $HOME/.gnubin
    for dir in $(ls /usr/local/opt); do
        gbin="/usr/local/opt/$dir/libexec/gnubin"
        if [[ -d "$gbin" ]]; then
            ls -1 $gbin | gxargs -ri ln -s -f $gbin/{} $HOME/.gnubin
        fi
    done

elif [[ "$OSTYPE" == "linux-gnu" ]]; then
    if hash apt-get 2>/dev/null;then
        sudo apt-get install -y $(cat apt-packages.txt | xargs)
    elif hash pacman 2>/dev/null; then
        sudo pacman -S $(cat pacman-packages.txt | xargs)
    else
        echo "only deb/ubuntu and arch flavors of linux are currently supported"
    fi
else
    echo "only linux and mac are supported"
    exit 1
fi

# install tmux fonts
git clone https://github.com/powerline/fonts.git --depth=1
cd fonts
./install.sh
cd ..
rm -rf fonts

git clone https://github.com/gpakosz/.tmux.git $HOME/.tmux
ln -s -f $HOME/.tmux/.tmux.conf $HOME/.
cp $HOME/.tmux/.tmux.conf.local $HOME/.

# Customize tmux
sed -i.bak -e 's/tmux_conf_theme_left_separator/#tmux_conf_theme_left_separator/g' \
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

# install oh-my-zsh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/robbyrussell/oh-my-zsh/master/tools/install.sh) --unattended"

# Set up zshrc options
sed -i.bak -e 's/ZSH_THEME="robbyrussell"/ZSH_THEME="agnoster"/'

# add mac specific options and gnu bins to path
if [[ "$OSTYPE" == "darwin"* ]]; then
    cat << 'EOF' >> ~/.zshrc
export HOMEBREW_CELLAR="/usr/local/Cellar"
EOF
    # make all the gnu binaries default
    echo "export PATH=$HOME/.gnubin:$PATH" >> ~/.zshrc
fi

# if on ubuntu set default editor to vim
if  hash update-alternatives 2>/dev/null; then
    sudo update-alternatives --set editor /usr/bin/vim.basic
fi

cat << 'EOF' >> ~/.zshrc
export LANG=en_US.UTF-8
#alias nuke_docker='docker rm --force $(docker ps -a -q)'
autoload -U +X bashcompinit && bashcompinit
export EDITOR='vim'
export SSH_KEY_PATH="~/.ssh/rsa_id"
prompt_context(){}
export GPG_TTY=`tty`
bindkey '^R' history-incremental-search-backward
#source <(helm completion zsh)
#source <(kubectl completion zsh)
#source /usr/local/etc/bash_completion.d/az
fpath+=${ZDOTDIR:-~}/.zsh_functions
tmux attach || tmux new
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
cd $HOME/.vim && git checkout heavenly && git submodule init && git submodule update && cd $current_dir
ln -s -f $HOME/.vim/.vimrc $HOME/.vimrc
ln -s -f $HOME/.vim/.vimrc.local $HOME/.vimrc.local

# set zsh as default shell and start zsh
sudo chsh $USER -s $(which zsh)
exec zsh -l
