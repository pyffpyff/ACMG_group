from setuptools import setup, find_packages
#get environment for agent name/IDENTIFIER
packages = find_packages('.')
package = packages[0]

setup(
    name = package + 'agent',
     version = "0.1",
     install_requires = ['volttron'],
     packages = packages,
     entry_points ={
        'setuptools.installation': [
            'eggsecutable = ' + package + '.utilityagent:main',
        ]
    }     
)