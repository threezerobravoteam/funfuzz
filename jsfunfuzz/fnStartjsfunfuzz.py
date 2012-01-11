#!/usr/bin/env python
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import platform
import shutil
import subprocess
import time

from multiprocessing import cpu_count
from tempfile import NamedTemporaryFile
from traceback import format_exc

verbose = False  # Turn this to True to enable verbose output for debugging.
showCapturedCommands = False

########################
#  Platform Detection  #
########################

def macType():
    # Script has only been tested on Snow Leopard and Lion.
    assert 6 <= int(platform.mac_ver()[0].split('.')[1]) <= 7
    isSL = isMac and platform.mac_ver()[0].split('.')[1] == '6' \
        and platform.mac_ver()[0].split('.') >= ['10', '6']
    amiLion = isMac and platform.mac_ver()[0].split('.')[1] == '7' \
        and platform.mac_ver()[0].split('.') >= ['10', '7']
    return (isSL, amiLion)

assert platform.system() in ('Windows', 'Linux', 'Darwin')
isMac = False
if platform.system() == 'Darwin':
    isMac = True
    (isSnowLeopard, isLion) = macType()

if platform.system() == 'Windows':
    assert 'Windows-XP' not in platform.platform()

#####################
#  Shell Functions  #
#####################

def vdump(inp):
    '''
    This function appends the word 'DEBUG' to any verbose output.
    '''
    if verbose:
        print 'DEBUG -', inp

def normExpUserPath(p):
    return os.path.normpath(os.path.expanduser(p))

def captureStdout(cmd, ignoreStderr=False, combineStderr=False, ignoreExitCode=False,
                  currWorkingDir=os.getcwdu()):
    '''
    This function captures standard output into a python string.
    '''
    if showCapturedCommands:
        print ' '.join(cmd)
    p = subprocess.Popen(cmd,
        stdin = subprocess.PIPE,
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT if combineStderr else subprocess.PIPE,
        cwd=currWorkingDir)
    (stdout, stderr) = p.communicate()
    if not ignoreExitCode and p.returncode != 0:
        # Potential problem area: Note that having a non-zero exit code does not mean that the
        # operation did not succeed, for example when compiling a shell. A non-zero exit code can
        # appear even though a shell compiled successfully. This issue has been bypassed in the
        # makeShell function in autoBisect.
        # Pymake in builds earlier than revision 232553f741a0 did not support the '-s' option.
        if 'no such option: -s' not in stdout:
            print 'Nonzero exit code from ' + repr(cmd)
            print stdout
        if stderr is not None:
            print stderr
        # Pymake in builds earlier than revision 232553f741a0 did not support the '-s' option.
        if 'no such option: -s' not in stdout:
            raise Exception('Nonzero exit code')
    if not combineStderr and not ignoreStderr and len(stderr) > 0:
        print 'Unexpected output on stderr from ' + repr(cmd)
        print stdout, stderr
        raise Exception('Unexpected output on stderr')
    if showCapturedCommands:
        print stdout
        if stderr is not None:
            print stderr
    return stdout.rstrip()

def timeShellFunction(command, cwd=os.getcwdu()):
    print 'Running `%s` now..' % ' '.join(command)
    startTime = time.time()
    retVal = subprocess.call(command, cwd=cwd)
    endTime = time.time()
    print '`' + ' '.join(command) + '` took %.3f seconds.\n' % (endTime - startTime)
    return retVal

def bashDate():
    '''
    Equivalent of: assert subprocess.check_output(['Date'])[:-1] == currDateTime
    '''
    currTz = time.tzname[0] if time.daylight == 1 else time.tzname[1]
    currAscDateTime = time.asctime( time.localtime(time.time()) )
    currDateTime = currAscDateTime[:-4] + currTz + ' ' + currAscDateTime[-4:]
    return currDateTime

##############################
#  startjsfunfuzz Functions  #
##############################

def hgHashAddToFuzzPath(fuzzPath, repoDir):
    '''
    This function finds the mercurial revision and appends it to the directory name.
    It also prompts if the user wants to continue, should the repository not be on tip.
    '''
    hgIdCmdList = ['hg', 'identify', '-i', '-n', '-b', repoDir]
    vdump('About to start running `' + ' '.join(hgIdCmdList) + '` ...')
    hgIdFull = captureStdout(hgIdCmdList, currWorkingDir=repoDir)
    hgIdChangesetHash = hgIdFull.split(' ')[0]
    hgIdLocalNum = hgIdFull.split(' ')[1]
    hgIdBranch = captureStdout(['hg', 'id', '-t'], currWorkingDir=repoDir)
    onDefaultTip = True
    if 'tip' not in hgIdBranch:
        print 'The repository is at this changeset -', hgIdLocalNum + ':' + hgIdChangesetHash
        notOnDefaultTipApproval = str(
            raw_input('Not on default tip! Are you sure you want to continue? (y/n): '))
        if notOnDefaultTipApproval == ('y' or 'yes'):
            onDefaultTip = False
        else:
            switchToDefaultTipApproval = str(
                raw_input('Do you want to switch to the default tip? (y/n): '))
            if switchToDefaultTipApproval == ('y' or 'yes'):
                subprocess.check_call(['hg', 'up', 'default'], cwd=repoDir)
            else:
                raise Exception('Not on default tip.')
    fuzzPath = '-'.join([fuzzPath, hgIdLocalNum, hgIdChangesetHash])
    vdump('Finished running `' + ' '.join(hgIdCmdList) + '`.')
    return normExpUserPath(fuzzPath), onDefaultTip

def patchHgRepoUsingMq(patchLoc, cwd=os.getcwdu()):
    # We may have passed in the patch with or without the full directory.
    p = os.path.abspath(normExpUserPath(patchLoc))
    pname = os.path.basename(p)
    assert (p, pname) != ('','')
    subprocess.check_call(['hg', 'qimport', p], cwd=cwd)
    vdump("Patch qimport'ed.")
    try:
        subprocess.check_call(['hg', 'qpush', pname], cwd=cwd)
        vdump("Patch qpush'ed.")
    except subprocess.CalledProcessError:
        subprocess.check_call(['hg', 'qpop'], cwd=cwd)
        subprocess.check_call(['hg', 'qdelete', pname], cwd=cwd)
        print 'You may have untracked .rej files in the repository.'
        print '`hg st` output of the repository in ' + cwd + ' :'
        subprocess.check_call(['hg', 'st'], cwd=cwd)
        hgPurgeAns = str(raw_input('Do you want to run `hg purge`? (y/n): '))
        assert hgPurgeAns.lower() in ('y', 'n')
        if hgPurgeAns == 'y':
            subprocess.check_call(['hg', 'purge'], cwd=cwd)
        raise Exception(format_exc())
    return pname

def autoconfRun(cwd):
    '''
    Sniff platform and run different autoconf types:
    '''
    if platform.system() == 'Darwin':
        subprocess.check_call(['autoconf213'], cwd=cwd)
    elif platform.system() == 'Linux':
        subprocess.check_call(['autoconf2.13'], cwd=cwd)
    elif platform.system() == 'Windows':
        subprocess.check_call(['sh', 'autoconf-2.13'], cwd=cwd)

def cfgJsBin(archNum, compileType, threadsafe, configure, objdir):
    '''
    This function configures a js binary depending on the parameters.
    '''
    cfgCmdList = []
    cfgEnvList = os.environ
    # For tegra Ubuntu, no special commands needed, but do install Linux prerequisites,
    # do not worry if build-dep does not work, also be sure to apt-get zip as well.
    if (archNum == '32') and (os.name == 'posix') and (os.uname()[1] != 'tegra-ubuntu'):
        # 32-bit shell on Mac OS X 10.6
        if isMac and isSnowLeopard:
            cfgEnvList['CC'] = 'gcc-4.2 -arch i386'
            cfgEnvList['CXX'] = 'g++-4.2 -arch i386'
            cfgEnvList['HOST_CC'] = 'gcc-4.2'
            cfgEnvList['HOST_CXX'] = 'g++-4.2'
            cfgEnvList['RANLIB'] = 'ranlib'
            cfgEnvList['AR'] = 'ar'
            cfgEnvList['AS'] = '$CC'
            cfgEnvList['LD'] = 'ld'
            cfgEnvList['STRIP'] = 'strip -x -S'
            cfgEnvList['CROSS_COMPILE'] = '1'
            cfgCmdList.append('sh')
            cfgCmdList.append(os.path.normpath(configure))
            cfgCmdList.append('--target=i386-apple-darwin8.0.0')
        # 32-bit shell on Mac OS X 10.7 Lion
        elif isMac and isLion:
            cfgEnvList['CC'] = 'clang -Qunused-arguments -fcolor-diagnostics -arch i386'
            cfgEnvList['CXX'] = 'clang++ -Qunused-arguments -fcolor-diagnostics -arch i386'
            cfgEnvList['HOST_CC'] = 'clang -Qunused-arguments -fcolor-diagnostics'
            cfgEnvList['HOST_CXX'] = 'clang++ -Qunused-arguments -fcolor-diagnostics'
            cfgEnvList['RANLIB'] = 'ranlib'
            cfgEnvList['AR'] = 'ar'
            cfgEnvList['AS'] = '$CC'
            cfgEnvList['LD'] = 'ld'
            cfgEnvList['STRIP'] = 'strip -x -S'
            cfgEnvList['CROSS_COMPILE'] = '1'
            cfgCmdList.append('sh')
            cfgCmdList.append(os.path.normpath(configure))
            cfgCmdList.append('--target=i386-apple-darwin8.0.0')
        # 32-bit shell on 32/64-bit x86 Linux
        elif (os.uname()[0] == "Linux") and (os.uname()[4] != 'armv7l'):
            # apt-get `ia32-libs gcc-multilib g++-multilib` first, if on 64-bit Linux.
            cfgEnvList['PKG_CONFIG_LIBDIR'] = '/usr/lib/pkgconfig'
            cfgEnvList['CC'] = 'gcc -m32'
            cfgEnvList['CXX'] = 'g++ -m32'
            cfgEnvList['AR'] = 'ar'
            cfgCmdList.append('sh')
            cfgCmdList.append(os.path.normpath(configure))
            cfgCmdList.append('--target=i686-pc-linux')
        # 32-bit shell on ARM (non-tegra ubuntu)
        elif os.uname()[4] == 'armv7l':
            cfgEnvList['CC'] = '/opt/cs2007q3/bin/gcc'
            cfgEnvList['CXX'] = '/opt/cs2007q3/bin/g++'
            cfgCmdList.append('sh')
            cfgCmdList.append(os.path.normpath(configure))
        else:
            cfgCmdList.append('sh')
            cfgCmdList.append(os.path.normpath(configure))
    # 64-bit shell on Mac OS X 10.7 Lion
    elif (archNum == '64') and (isMac and not isSnowLeopard):
        cfgEnvList['CC'] = 'clang -Qunused-arguments -fcolor-diagnostics'
        cfgEnvList['CXX'] = 'clang++ -Qunused-arguments -fcolor-diagnostics'
        cfgEnvList['AR'] = 'ar'
        cfgCmdList.append('sh')
        cfgCmdList.append(os.path.normpath(configure))
        cfgCmdList.append('--target=x86_64-apple-darwin11.2.0')
    elif (archNum == '64') and (os.name == 'nt'):
        cfgCmdList.append('sh')
        cfgCmdList.append(os.path.normpath(configure))
        cfgCmdList.append('--host=x86_64-pc-mingw32')
        cfgCmdList.append('--target=x86_64-pc-mingw32')
    else:
        cfgCmdList.append('sh')
        cfgCmdList.append(os.path.normpath(configure))

    if compileType == 'dbg':
        cfgCmdList.append('--disable-optimize')
        cfgCmdList.append('--enable-debug')
    elif compileType == 'opt':
        cfgCmdList.append('--enable-optimize')
        cfgCmdList.append('--disable-debug')
        cfgCmdList.append('--enable-profiling')  # needed to obtain backtraces on opt shells

    cfgCmdList.append('--enable-methodjit')
    cfgCmdList.append('--enable-type-inference')
    # Fuzzing tweaks for more useful output, bug 706433
    cfgCmdList.append('--enable-more-deterministic')
    cfgCmdList.append('--disable-tests')

    if os.name != 'nt':
        if ((os.uname()[0] == "Linux") and (os.uname()[4] != 'armv7l')) or isMac:
            cfgCmdList.append('--enable-valgrind')
            # ccache does not seem to work on Mac.
            if not isMac:
                cfgCmdList.append('--with-ccache')
        # ccache is not applicable for Windows and non-Tegra Ubuntu ARM builds.
        elif os.uname()[1] == 'tegra-ubuntu':
            cfgCmdList.append('--with-ccache')
            cfgCmdList.append('--with-arch=armv7-a')

    if threadsafe:
        cfgCmdList.append('--enable-threadsafe')
        cfgCmdList.append('--with-system-nspr')
    # Works-around "../editline/libeditline.a: No such file or directory" build errors by using
    # readline instead of editline.
    #cfgCmdList.append('--enable-readline')

    if os.name == 'nt':
        # Only tested to work for pymake.
        counter = 0
        for entry in cfgCmdList:
            if os.sep in entry:
                cfgCmdList[counter] = cfgCmdList[counter].replace(os.sep, '\\\\')
            counter = counter + 1

    vdump('This is the configure command (environment variables not included):')
    vdump('%s\n' % ' '.join(cfgCmdList))

    # On Windows, install prerequisites at https://developer.mozilla.org/En/Windows_SDK_versions
    # Note that on Windows, redirecting stdout to subprocess.STDOUT does not work on Python 2.6.5.
    if verbose:
        subprocess.check_call(cfgCmdList, stderr=subprocess.STDOUT, cwd=objdir, env=cfgEnvList)
    else:
        fnull = open(os.devnull, 'w')
        subprocess.check_call(
            cfgCmdList, stdout=fnull, stderr=subprocess.STDOUT, cwd=objdir, env=cfgEnvList)
        fnull.close()

def shellName(archNum, compileType, extraID, vgSupport):
    return '-'.join(x for x in ['js', compileType, archNum,
                     "vg" if vgSupport else "", extraID, platform.system().lower(),
                     '.exe' if platform.system() == 'Windows' else ''] if x)

def compileCopy(archNum, compileType, extraID, usePymake, repoDir, destDir, objDir, vgSupport):
    '''
    This function compiles and copies a binary.
    '''
    jobs = (cpu_count() * 3) // 2
    compiledNamePath = normExpUserPath(
        os.path.join(objDir, 'js' + ('.exe' if platform.system() == 'Windows' else '')))
    try:
        if usePymake:
            out = captureStdout(
                ['python', '-OO',
                 os.path.normpath(os.path.join(repoDir, 'build', 'pymake', 'make.py')),
                 '-j' + str(jobs), '-s'], combineStderr=True, currWorkingDir=objDir)
            # Pymake in builds earlier than revision 232553f741a0 did not support the '-s' option.
            if 'no such option: -s' in out:
                out = captureStdout(
                    ['python', '-OO',
                     os.path.normpath(os.path.join(repoDir, 'build', 'pymake', 'make.py')),
                     '-j' + str(jobs)], combineStderr=True, currWorkingDir=objDir)
        else:
            out = captureStdout(
                ['make', '-C', objDir, '-j' + str(jobs), '-s'],
                combineStderr=True, ignoreExitCode=True, currWorkingDir=objDir)
    except Exception as e:
        # Sometimes a non-zero error can be returned during the make process, but eventually a
        # shell still gets compiled.
        if os.path.exists(compiledNamePath):
            print 'A shell was compiled even though there was a non-zero exit code. Continuing...'
        else:
            print out
            raise Exception("`make` did not result in a js shell, '" + repr(e) + "' thrown.")

    if not os.path.exists(compiledNamePath):
        print out
        raise Exception("`make` did not result in a js shell, no exception thrown.")
    else:
        newNamePath = normExpUserPath(
            os.path.join(destDir, shellName(archNum, compileType, extraID, vgSupport)))
        shutil.copy2(compiledNamePath, newNamePath)
        return newNamePath

####################
#  Test Functions  #
####################

def archOfBinary(b):
    '''
    This function tests if a binary is 32-bit or 64-bit.
    '''
    unsplitFiletype = captureStdout(['file', b])
    filetype = unsplitFiletype.split(':', 1)[1]
    if 'universal binary' in filetype:
        raise Exception("I don't know how to deal with multiple-architecture binaries")
    if '386' in filetype or '32-bit' in filetype:
        assert '64-bit' not in filetype
        return '32'
    if '64-bit' in filetype:
        assert '32-bit' not in filetype
        return '64'

def exitCodeDbgOptOrJsShellXpcshell(shell, dbgOptOrJsShellXpcshell, cwd=os.getcwdu()):
    '''
    This function returns the exit code after testing the shell.
    '''
    contents = ''
    contentsList = []
    cmdList = []
    f = NamedTemporaryFile()

    cmdList.append(shell)
    if dbgOptOrJsShellXpcshell == 'dbgOpt':
        contents = 'gczeal()'
    elif dbgOptOrJsShellXpcshell == 'jsShellXpcshell':
        contents = 'Components'
        # To run xpcshell, command is `./run-mozilla.sh ./xpcshell testcase.js`
        # js shells do not have the middle parameter, so they will mis-understand and think that
        # ./xpcshell is the testcase they should run instead.
        if 'run-mozilla' in shell:
            cmdList.append('./xpcshell')
            assert len(cmdList) == 2
        else:
            assert len(cmdList) == 1

    contentsList.append(contents)
    f.writelines(contentsList)
    f.flush()  # Important! Or else nothing will be in the file when the js shell executes the file.

    cmdList.append(f.name)
    vdump(' '.join(cmdList))
    if verbose:
        retCode = subprocess.call(cmdList, stderr=subprocess.STDOUT, cwd=cwd)
    else:
        fnull = open(os.devnull, 'w')
        retCode = subprocess.call(cmdList, stdout=fnull, stderr=subprocess.STDOUT, cwd=cwd)
        fnull.close()

    # Verbose logging.
    vdump('The contents of ' + f.name + ' is:')
    if verbose:
        # Go back to the beginning of the file to print if verbose. It hasn't yet been closed.
        f.seek(0)
    print ''.join([line for line in f.readlines() if verbose])
    f.close()

    vdump('The return code is: ' + str(retCode))
    return retCode

def testJsShellOrXpcshell(sname, cwd=os.getcwdu()):
    '''
    This function tests if a binary is a js shell or xpcshell.
    '''
    exitCode = exitCodeDbgOptOrJsShellXpcshell(sname, 'jsShellXpcshell', cwd=cwd)

    # The error code for xpcshells when passing in the Components function should be 0.
    if exitCode == 0:
        return 'xpcshell'
    # js shells don't have Components compiled in by default.
    elif exitCode == 3:
        return 'jsShell'
    else:
        raise Exception('Unknown exit code after testing if js shell or xpcshell: ' + str(exitCode))

def testDbgOrOpt(jsShellName, cwd=os.getcwdu()):
    '''
    This function tests if a binary is a debug or optimized shell.
    '''
    exitCode = exitCodeDbgOptOrJsShellXpcshell(jsShellName, 'dbgOpt', cwd=cwd)

    # The error code for debug shells when passing in the gczeal() function should be 0.
    if exitCode == 0:
        return 'dbg'
    # Optimized shells don't have gczeal() compiled in by default.
    elif exitCode == 3:
        return 'opt'
    else:
        raise Exception('Unknown exit code after testing if debug or opt: ' + exitCode)

def testDbgOrOptGivenACompileType(jsShellName, compileType, cwd=os.getcwdu()):
    '''
    This function tests if a binary is a debug or optimized shell given a compileType.
    '''
    exitCode = exitCodeDbgOptOrJsShellXpcshell(jsShellName, 'dbgOpt', cwd=cwd)

    vdump('The error code for debug shells should be 0.')
    vdump('The error code for opt shells should be 3.')
    vdump('The actual error code for ' + jsShellName + ' now, is: ' + str(exitCode))

    # The error code for debug shells when passing in the gczeal() function should be 0.
    if compileType == 'dbg' and exitCode != 0:
        print 'ERROR: A debug shell tested with gczeal() should return "0" as the error code.'
        print 'compileType is: ' + compileType
        print 'exitCode is: ' + str(exitCode)
        print
        raise Exception('The compiled binary is not a debug shell.')
    # Optimized shells don't have gczeal() compiled in by default.
    elif compileType == 'opt' and exitCode != 3:
        print 'ERROR: An optimized shell tested with gczeal() should return "3" as the error code.'
        print 'compileType is: ' + compileType
        print 'exitCode is: ' + str(exitCode)
        print
        raise Exception('The compiled binary is not an optimized shell.')

if __name__ == '__main__':
    pass
