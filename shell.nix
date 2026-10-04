{ pkgs ? import <nixpkgs> { } }:
pkgs.mkShell rec {
  name = "dev-shell";
  buildInputs = with pkgs; [
    zsh
    python3
    poetry
    uv
    zlib
    stdenv.cc.cc.lib
  ];
  LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath buildInputs;
  shellHook = ''
    # If interactive and not already in zsh, switch to zsh
    if [ -n "''${PS1-}" ] && [ -z "''${ZSH_VERSION-}" ]; then
      export SHELL=${pkgs.zsh}/bin/zsh
      exec ${pkgs.zsh}/bin/zsh -i
    fi
  '';
}
