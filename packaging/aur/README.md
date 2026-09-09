# AUR packaging

The source of truth for the `talkey-git` AUR package. The AUR repo is a separate
git repo that holds only these files, at its root - it is not a checkout of this
one. Clone it with:

```bash
git clone ssh://aur@aur.archlinux.org/talkey-git.git
```

The AUR accepts pushes to `master` and no other branch, so a fresh clone whose
`init.defaultBranch` is `main` needs renaming before the first push.

## Layout note

The package is `talkey-git`, but the data tree is `/usr/share/talkey`. The
scripts probe that path to find `lib/common.sh` and the rest, and it has to match
whether talkey came from a package or a checkout. `$pkgname` carries the `-git`;
`$_name` names the project. Only doc and licenses use `$pkgname`, which is what
Arch expects.

## Publishing an update

```bash
cd talkey-git                       # the AUR clone, on branch master
cp /path/to/talkey/packaging/aur/{PKGBUILD,talkey-git.install} .
makepkg -f                          # rewrites pkgver= from the clone
makepkg --printsrcinfo > .SRCINFO   # must be regenerated after that
git add PKGBUILD .SRCINFO talkey-git.install
git commit -m "talkey-git <version>"
git push origin master
```

`.SRCINFO` is a dotfile, so `git add *` silently skips it and the AUR hook
rejects the push. Add it by name.

Building in the AUR clone leaves artifacts behind, so it wants a `.gitignore` of
its own - same as diffstalker-git's:

```
*.pkg.tar.zst
src/
pkg/
talkey-git/
```

`talkey-git/` is the clone makepkg makes of this repo, from `source=`.

Being a VCS package, there is nothing to tag and no tarball to checksum -
`sha256sums=('SKIP')` is correct for a git source, and users always build the
current `main`.
