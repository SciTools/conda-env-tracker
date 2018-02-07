#!/usr/bin/env python

# conda execute
# env:
#  - gitpython
#  - conda-build-all
#  - yaml
# channels:
#  - conda-forge

from __future__ import print_function

import datetime
import contextlib
import logging
import os
import shutil
import tempfile

import conda.resolve
import conda.api
import conda_build_all.version_matrix
from git import Repo
import yaml

from conda_gitenv import manifest_branch_prefix


def resolve_spec(spec_fh):
    """
    Given an open file handle to an env.spec, return a list of strings containing
    '<channel_url>\t<pkg_name>' for each package resolved.

    """
    spec = yaml.safe_load(spec_fh)
    env_spec = spec.get('env', [])
    index = conda.api.get_index(spec.get('channels', []), prepend=False, use_cache=False)
    resolver = conda.resolve.Resolve(index)
    packages = resolver.solve(env_spec)
    # Use the resolver to sort packages into the appropriate dependency
    # order.
    sorted_packages = resolver.dependency_sort({dist.name: dist for dist in packages})

    pkgs = []
    for pkg in sorted_packages:
        pkg_info = index[pkg]
        pkgs.append('\t'.join([pkg_info['channel'],
                               pkg_info['fn'][:-len('.tar.bz2')]])), 
    return pkgs


def build_manifest_branches(repo):
    for remote in repo.remotes:
        remote.fetch()

    for branch in repo.branches:
        name = branch.name
        if name.startswith(manifest_branch_prefix):
            continue
        branch.checkout()
        spec_fname = os.path.join(repo.working_dir, 'env.spec')
        if not os.path.exists(spec_fname):
            # Skip branches which don't have a spec.
            continue
        with open(spec_fname, 'r') as fh:
            pkgs = resolve_spec(fh)
            # Cache the contents of the env.spec file from the source branch.
            fh.seek(0)
            spec_lines = fh.readlines()
        manifest_branch_name = '{}{}'.format(manifest_branch_prefix, name)
        if manifest_branch_name in repo.branches:
            manifest_branch = repo.branches[manifest_branch_name]
        else:
            manifest_branch = repo.create_head(manifest_branch_name)
        manifest_branch.checkout()
        manifest_path = os.path.join(repo.working_dir, 'env.manifest')
        with open(manifest_path, 'w') as fh:
            fh.write('\n'.join(pkgs))
        # Write the env.spec from the source branch into the manifest branch.
        with open(spec_fname, 'w') as fh:
            fh.writelines(spec_lines)
        repo.index.add([manifest_path, spec_fname])
        if repo.is_dirty():
            repo.index.commit('Manifest update from {:%Y-%m-%d %H:%M:%S}.'
                              ''.format(datetime.datetime.now()))


@contextlib.contextmanager
def tempdir(prefix='tmp'):
    """A context manager for creating and then deleting a temporary directory."""
    tmpdir = tempfile.mkdtemp(prefix=prefix)
    try:
        yield tmpdir
    finally:
        if os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir)


def create_tracking_branches(repo):
    """
    Create local tracking branches for each of the remote's branches.
    Ignore `HEAD` because it isn't a branch, and ignore the default
    remote branch (e.g. `master`) because that will already have a
    local tracking branch.

    """
    heads_to_skip = ['HEAD'] + [branch.name for branch in repo.branches]
    for ref in repo.remotes.origin.refs:
        if ref.remote_head not in heads_to_skip:
            # Create the branch from the remote branch, and point it to
            # track the origin's branch.
            repo.create_head(ref.remote_head, ref).set_tracking_branch(ref)


def configure_parser(parser):
    parser.add_argument('repo_uri', help='Repo to use for environment tracking.')
    parser.add_argument('--verbose', '-v', action='store_true')
    parser.set_defaults(function=handle_args)
    return parser


def handle_args(args):
    log_level = logging.WARN
    if args.verbose:
        log_level = logging.DEBUG
    with conda_build_all.version_matrix.override_conda_logging(log_level):
        with tempdir() as repo_directory:
            repo = Repo.clone_from(args.repo_uri, repo_directory)
            create_tracking_branches(repo)
            build_manifest_branches(repo)
            for branch in repo.branches:
                if branch.name.startswith(manifest_branch_prefix):
                    remote_branch = branch.tracking_branch()
                    if remote_branch is None or branch.commit != remote_branch.commit:
                        print('Pushing changes to {}'.format(branch.name))
                        repo.remotes.origin.push(branch)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Track environment specifications using a git repo.')
    configure_parser(parser)
    args = parser.parse_args()
    return args.function(args)


if __name__ == '__main__':
    main()
