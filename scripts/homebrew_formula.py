"""Write the Homebrew formula for a release: `python scripts/homebrew_formula.py <sdist-url> <sha256> > rumo.rb`.

The formula has no `resource` blocks yet: the release workflow (and a local test) runs
`brew update-python-resources` on it, which pins every dependency from PyPI.
"""

import sys

TEMPLATE = '''class Rumo < Formula
  include Language::Python::Virtualenv

  desc "Your dev work, past, now and later: Markdown files and a terminal Timeline"
  homepage "https://github.com/pedrobuzzi/rumo"
  url "{url}"
  sha256 "{sha256}"
  license "MIT"

  depends_on "python@3.13"

  def install
    # Isolated from Homebrew Python's global site-packages: a legacy `*-nspkg.pth` left there by
    # packages installed with pip breaks pip's builds of the resources inside this virtualenv.
    virtualenv_install_with_resources(system_site_packages: false)
  end

  test do
    assert_match "rumo", shell_output("#{{bin}}/rumo --version")
    ENV["RUMO_DIR"] = testpath/"items"
    ENV["RUMO_CONFIG"] = testpath/"config.toml"
    assert_match "# language:", shell_output("#{{bin}}/rumo index")
  end
end
'''


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python scripts/homebrew_formula.py <sdist-url> <sha256>")
    sys.stdout.write(TEMPLATE.format(url=sys.argv[1], sha256=sys.argv[2]))


if __name__ == "__main__":
    main()
