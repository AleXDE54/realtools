# realtools
real's own free software manager

## Install:

```bash
curl -sSL https://raw.githubusercontent.com/AleXDE54/realtools/main/install.sh | bash
```

### Usage:

```bash
rtls install username/github-repo (--bin) (--force)
```

#### --bin
This subcommand is used for autobuilding to the binary data with pyinstaller command (pyinstaller needs to be installed and added to the aliases)


#### --force
This subcommand is forcing the package manager to install dependensys with PIP command (not really recomended)

THIS WILL WORK IF REPO HAS A realtools.txt AND python file

# Example repo

```bash
rtls install AleXDE54/lofitty
```
