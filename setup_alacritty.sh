#!/usr/bin/env bash
set -x

setup_completions(){
    if [[ ! -f "${ZDOTDIR:-~}/.zsh_functions/_alacritty" ]]; then
        mkdir -p ${ZDOTDIR:-~}/.zsh_functions
        echo 'fpath+=${ZDOTDIR:-~}/.zsh_functions' >> ${ZDOTDIR:-~}/.zshrc
        curl https://raw.githubusercontent.com/alacritty/alacritty/master/extra/completions/_alacritty -o "${ZDOTDIR:-~}/.zsh_functions/_alacritty"
    fi
}


setup_man_pages(){
    man_path="/usr/local/share/man/man1"
    man_page_file="${man_path}/alacritty.1.gz"
    if [[ ! -f "${man_page_file}" ]]; then
        sudo mkdir -p ${man_path}
        curl -O httpsaa/raw.githubusercontent.com/alacritty/alacritty/master/extra/alacritty.man
        gzip -c alacritty.man | sudo tee /usr/local/share/man/man1/alacritty.1.gz > /dev/null
        rm alacritty.man
    fi
}

setup_terminfo(){
    curl -O https://raw.githubusercontent.com/alacritty/alacritty/master/extra/alacritty.info
    sudo tic -xe alacritty,alacritty-direct alacritty.info
    rm alacritty.info
}

install_mac(){
    if ! hash brew 2>/dev/null; then
        /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"
    fi

    brew update && brew cask install alacritty

    if ! hash gzip 2>/dev/null; then
        brew install gzip
    fi
}

install_linux(){
    if hash apt-get 2>/dev/null;then
        sudo add-apt-repository ppa:mmstick76/alacritty
        sudo apt-get update && sudo apt-get install -y gzip alacritty
    elif  hash pacman 2>/dev/null; then
        pacman -S alacritty gzip
    else
        echo "Only deb/ubuntu and arch based distros are supported"
        exit 1
    fi
}

if [[ "$OSTYPE" == "darwin"* ]]; then
    install_mac
elif [[ "$OSTYPE" == "linux-gnu" ]]; then
    install_linux
fi

setup_man_pages
setup_completions
setup_terminfo

mkdir -p $HOME/.config/alacritty
sudo ln -s -f $(pwd)/terminal/configs/alacritty.yml $HOME/.config/alacritty/.

