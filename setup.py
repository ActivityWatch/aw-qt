#!/usr/bin/env python

from setuptools import setup

setup(name='aw-qt',
      version='0.1',
      description='Trayicon for ActivityWatch in Qt',
      author='Erik Bjäreholt',
      author_email='erik@bjareho.lt',
      url='https://github.com/ActivityWatch/aw-trayicon',
      packages=['aw_qt'],
      install_requires=[
          'aw-core>=0.1',
          'PyQt5>=5.8,<5.10'
      ],
      dependency_links=[
          'https://github.com/ActivityWatch/aw-core/tarball/master#egg=aw-core-0.1.0'
      ],
      entry_points={
          'console_scripts': ['aw-qt = aw_qt:main']
      },
      classifiers=[
          'Programing Language :: Python :: 3'
      ])
