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
* thefuck
* Powerline fonts https://github.com/powerline/fonts
* zsh-syntax-highlighting https://github.com/zsh-users/zsh-syntax-highlighting

### Examples of some of the setup.

```
# tmux and .tmux
git clone git@github.com:gpakosz/.tmux.git
ln -s -f .tmux/.tmux.conf
ln -s -f tmux.conf.local $HOME/.tmux.conf.local

# vim
mkdir -p ~/.vim
cp -r /path/to/my/repo/vim/. ~/.vim/.
ln -s -f vimrc ~/.vimrc

# I put the following into a dot directory so I don't have to look at it when I do ls on ~
git clone git@github.com:zsh-users/zsh-syntax-highlighting.git ~/.zsh-syntax-highlighting

# optional scripts to clean docker cache (orphaned images), and python cache
cp scripts/clean_docker_cache /some/dir/in/$PATH
cp scropts/clean_python_cache /some/dir/in/$PATH

# Finally, put zshrc in place:
ln -s -f zshrc ~/.zshrc
source ~/.zshrc
```

