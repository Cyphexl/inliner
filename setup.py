from setuptools import setup

if __name__ == "__main__":
    setup(name='inliner',
          version='0.1.0',
          description='Human-readable inlining of Python code',
          url='http://github.com/willcrichton/inliner',
          author='Will Crichton',
          author_email='wcrichto@cs.stanford.edu',
          license='Apache 2.0',
          packages=['inliner'],
          install_requires=['astor', 'iterextras'],
          setup_requires=['pytest-runner'],
          tests_require=['pytest'],
          zip_safe=False)