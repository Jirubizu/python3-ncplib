from setuptools import setup, find_packages

from ncplib import __version__


setup(
    name="ncplib",
    version=".".join(map(str, __version__)),
    description="CRFS NCP library for Python 3.",
    author="Dave Hall",
    author_email="dhall@crfs.com",
    url="https://github.com/CRFS/python3-ncplib",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.5",
    ],
    extras_require={
        "dev":  [
            "flake8==2.5.4",
            "coverage==4.0.3",
            "hypothesis[datetime]==3.1.0",
        ],
    },
)
