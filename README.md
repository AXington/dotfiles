# linuxsetup
this is my linux setup that I keep at all times so I never lose my setup

It uses ZSH, OH-MY-ZSH, and tmux for most of it's configurations.

The tmux settings here are from https://github.com/gpakosz/.tmux

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

After these prereqs are installed, run:

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
ln -s -f gitconfig $HOME/.gitconfig
ln -s -f vim $HOME/.vim
ln -s -f vimrc $HOME/.vimrc
ln -s -f zshrc $HOME/.zshrc

# if you want to set your caps_lock button to control (highly recommended)
echo "setxkbmap -option ctrl:nocaps" >> $HOME/.local_settings
echo "xcape -e 'Caps_Lock=Control'" >> $HOME/.local_settings
```

