# linuxsetup
this is my dev and work setup that I keep at all times so I can quickly
set up new environments to my preferences and have a consistent workflow.
I also keep this with the readme as a reminder and to help friends who like my setup
to easily get set up with something like it.

It uses ZSH, OH-MY-ZSH, and tmux for most of it's configurations.

I also use VIM as my editor and vim mode in my terminal.

## Setup
To set it up first you must set up the following:

* ZSH
* OH-MY-ZSH
* vim
* tmux
* .tmux
* Powerline fonts https://github.com/powerline/fonts
* zsh-syntax-highlighting https://github.com/zsh-users/zsh-syntax-highlighting

### Examples of some of the setup.

```
# Install oh-my-zsh
# zsh should already be installed
# if mac: brew install zsh
# ubuntu: apt-get install -y zsh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/robbyrussell/oh-my-zsh/master/tools/install.sh)"

# Setup zsh syntax highlighting
git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting
# add zsh-syntax-highlighting to plugins in ~/.zshrc

# Setup tmux and .tmux
git clone git@github.com:gpakosz/.tmux.git
ln -s -f .tmux/.tmux.conf
cp .tmux/.tmux.conf.local ~/.tmux.conf.local
# I add several options, see setup_zsh_tmux_env.sh

# vim
git clone git@github.com:AXington/.vim.git
cd .vim && git checkout heavenly


# optional scripts to clean docker cache (orphaned images), and python cache
cp scripts/clean_docker_cache /usr/local/bin/$PATH
cp scripts/clean_python_cache /usr/local/bin/$PATH


Some/most of this should be already done as part of setup_zsh_tmux_env.sh

```

