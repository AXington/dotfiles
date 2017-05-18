# linuxsetup
this is my linux setup that I keep at all times so I never lose my setup

It uses ZSH, OH-MY-ZSH, and tmux for most of it's configurations.

The tmux settings here are from https://github.com/gpakosz/.tmux

![My Terminal](https://github.com/AXington/linuxsetup/blob/master/img/screenshot.png)

## Future notes
I'm currently looking into replacing oh-my-zsh with https://github.com/myzsh/myzsh
Currently what I need from it that makes me hesitant is docker tab completion and
python tab completion. However, it seems to be lighter weight than oh-my-zsh and plugins
for it are not as coupled to the themes as OMZ are.

## Setup
To set it up first you must set up the following:

* ZSH
* OH-MY-ZSH
* vim
* tmux
* thefuck
* xcape (linux only, if you want to override caps lock, see install instructions in submodule)
* Powerline fonts (see https://github.com/gpakosz/.tmux) for more information

###Experimental (WIP, untested, use at your own risk)

After the prerequs are installed, if you wish to setup git eport the following variables:

```
export GIT_EMAIL=you@yourdomain.extension
export GIT_NAME="Firstname Lastname"
export EDITOR="vim"
```

`setup.sh`

To add to your path add a .pathfile to your home directory and export your new path.

`export PATH='/path/to/your/stuff:$PATH'`

To add other settings, aliases, etc, add these to a `.local_settings` file in your home directory

###Known good

```
git clone git@github.com:AXington/linuxsetup.git
cd linuxsetup
git submodule update --init --recursive

ln -s -f gpakosz-tmux/.tmux.conf $HOME/.tmux.conf
ln -s -f tmux.conf.local $HOME/.tmux.conf.local
mkdir -p ~/.vim
cp -r vim/. ~/.vim/.
ln -s -f vimrc ~/.vimrc
ln -s -f zshrc ~/.zshrc
cp -r zsh-syntax-highlighting ~/.

# if you want to set your caps_lock button to control (highly recommended)
echo "setxkbmap -option ctrl:nocaps" >> $HOME/.local_settings
echo "xcape -e 'Caps_Lock=Control'" >> $HOME/.local_settings
```

