# Personal Setup
this is my dev and work setup that I keep at all times so I can quickly
set up new environments to my preferences and have a consistent workflow.
I also keep this with the readme as a reminder and to help friends who like my setup
to easily get set up with something like it.

It uses ZSH, OH-MY-ZSH, and tmux for most of it's configurations.

I also use VIM as my editor and vim mode in my terminal.

I also sometimes use vscode and Jetbrains IDE's, I have configs for those as well.

My terminal settings for different terminals are in terminal_conigs/

Alacritty is my terminal emulator of choice in most OSes (not Windows, but really I mostly choose not Windows), and can be found at: https://github.com/jwilm/alacritty

My terminal of choice for windows is wsltty, for when I absolutely have to use Windows.

## Terminal Setup

This setup uses the following third party applications, plugins, tools, repos, etc:
* zsh
* oh-my-zsh https://github.com/robbyrussell/oh-my-zsh
* vim
* .vim https://github.com/gpakosz/.vim (I use my personal fork on the heavenly branch, https://github.com/axington/.vim)
* tmux
* .tmux https://github.com/gpakosz/.tmux
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
# If tmux is not installed use apt, yum, pacman, or brew to install
# See readme of .tmux repo for more information as to what this offers.
# I also do some customization to my .tmux.conf.local
# For those and to get the powerline style status bar, see setup.sh under '# Customize tmux'
git clone git@github.com:gpakosz/.tmux.git
ln -s -f .tmux/.tmux.conf
cp .tmux/.tmux.conf.local ~/.tmux.conf.local

# vim
git clone git@github.com:AXington/.vim.git # use https://github.com/gpakosz/.vim if you don't want to use my fork, or create your own, see repo's README for more info
cd .vim && git checkout heavenly


# optional scripts to clean docker cache (orphaned images), and python cache
cp scripts/clean_docker_cache /usr/local/bin/$PATH
cp scripts/clean_python_cache /usr/local/bin/$PATH

# setup Alacritty
##  On mac `brew cask install alacritty`

## On ubuntu:
## add-apt-repository ppa:mmstick76/alacritty
## apt-get update
## apt-get install alacritty

## On arch
## pacman -S alacritty

# setup completions (possibly only needed on mac
mkdir -p ${ZDOTDIR:-~}/.zsh_functions
echo 'fpath+=${ZDOTDIR:-~}/.zsh_functions' >> ${ZDOTDIR:-~}/.zshrc
curl -o cp extra/completions/_alacritty ${ZDOTDIR:-~}/.zsh_functions/_alacritty ${ZDOTDIR:-~}/.zsh_functions/_alacritty

# setup terminfo, possibly only needed on mac
curl -O https://raw.githubusercontent.com/jwilm/alacritty/master/extra/alacritty.info && sudo tic -xe alacritty,alacritty-direct alacritty.info && rm alacritty.info

# setup man pages, possibly only needed on mac, needs gzip installed
sudo mkdir -p /usr/local/share/man/man1
curl -O https://raw.githubusercontent.com/jwilm/alacritty/master/extra/alacritty.man && gzip -c alacritty.man | sudo tee /usr/local/share/man/man1/alacritty.1.gz > /dev/null && rm alacritty.man


Some/most of this should be already done as part of setup.sh

```

