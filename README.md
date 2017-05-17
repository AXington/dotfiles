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
* xcape (on linux, see install instructions in submodule)
* python-virtualenv
* Powerline fonts (see https://github.com/gpakosz/.tmux) for more information

After these prereqs are installed, run setup.sh

To add to your path add a .pathfile to your home directory and export your new path.

`export PATH='/path/to/your/stuff:$PATH'`

To add other settings, aliases, etc, add these to a .local_settings file in your home directory

