from setuptools import find_packages, setup


with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="openmanusv2",
    version="2.2.1",
    author="mannaandpoem and OpenManus Team",
    author_email="mannaandpoem@gmail.com",
    description="Install and manage a production OpenManus Docker deployment",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/MohamedElsaeidy/OpenManus",
    project_urls={
        "Documentation": "https://github.com/MohamedElsaeidy/OpenManus#readme",
        "Source": "https://github.com/MohamedElsaeidy/OpenManus",
        "Issues": "https://github.com/MohamedElsaeidy/OpenManus/issues",
        "Releases": "https://github.com/MohamedElsaeidy/OpenManus/releases",
    },
    packages=find_packages(include=["openmanus_cli", "openmanus_cli.*"]),
    install_requires=[],
    keywords=["ai", "agent", "docker", "llm", "deployment"],
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Environment :: Console",
        "Topic :: Software Development :: Build Tools",
    ],
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "openmanus=openmanus_cli.cli:main",
        ],
    },
)
