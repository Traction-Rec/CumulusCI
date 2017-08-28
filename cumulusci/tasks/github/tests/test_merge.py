import ast
import httplib
import os
import shutil
import tempfile
import unittest

import requests
import responses

from datetime import datetime
from datetime import timedelta
from testfixtures import LogCapture

from cumulusci.tests.util import create_project_config
from cumulusci.tests.util import DummyOrgConfig
from cumulusci.core.config import ServiceConfig
from cumulusci.core.config import TaskConfig

from cumulusci.tasks.github import MergeBranch
from cumulusci.core.exceptions import GithubApiNotFoundError
from cumulusci.tasks.github.tests.util_github_api import GithubApiTestMixin


class TestMergeBranch(unittest.TestCase, GithubApiTestMixin):

    def setUp(self):

        # Set up the mock values
        self.repo_name = 'TestRepo'
        self.repo_owner = 'TestOwner'
        self.repo_api_url = 'https://api.github.com/repos/{}/{}'.format(
            self.repo_owner,
            self.repo_name,
        )
        self.branch = 'master'

        # Create the project config
        self.project_config = create_project_config(
            self.repo_name,
            self.repo_owner,
        )
        self.project_config.keychain.set_service(
            'github',
            ServiceConfig({ 
                'username': 'TestUser',
                'password': 'TestPass',
                'email': 'testuser@testdomain.com',
            })
        )
        
        #self.current_tag_sha = self._random_sha()
        #self.current_tag_commit_sha = self._random_sha()
        #self.current_tag_commit_date = datetime.utcnow()
        #self.last_tag_sha = self._random_sha()
        #self.last_tag_commit_sha = self._random_sha()
        #self.last_tag_commit_date = datetime.utcnow() - timedelta(days=1)

    def _create_task(self, task_config=None):
        if not task_config:
            
            task_config = {}
        task = MergeBranch(
            project_config = self.project_config,
            task_config = TaskConfig(task_config),
            org_config = DummyOrgConfig(),
        )
        return task

    def _mock_repo(self):
        api_url = self.repo_api_url
        
        expected_response = self._get_expected_repo(
            owner = self.repo_owner,
            name = self.repo_name,
        )
        responses.add(
            method=responses.GET,
            url=api_url,
            json=expected_response,
        )
        return expected_response

    def _mock_branch(self, branch, expected_response=None):
        api_url = '{}/branches/{}'.format(self.repo_api_url, branch)
        if not expected_response:
            expected_response = self._get_expected_branch(branch)
        responses.add(
            method = responses.GET,
            url = api_url,
            json = expected_response,
        )

    def _mock_branches(self, branches=None):
        api_url = '{}/branches'.format(self.repo_api_url)
        if branches:
            expected_response = branches
        else:
            expected_response = []
        
        master = self._get_expected_branch(
            'master', 
            self.project_config.repo_commit
        )
        expected_response = [master] + expected_response

        responses.add(
            method = responses.GET,
            url = api_url,
            json = expected_response,
        )
        return expected_response

    def _mock_branch_does_not_exist(self, branch):
        api_url = '{}/branches/{}'.format(self.repo_api_url, branch)
        expected_response = self._get_expected_not_found()
        responses.add(
            method = responses.GET,
            url = api_url,
            status = 404,
            json = expected_response,
        )

    def _mock_merge(self, conflict=None):
        api_url = '{}/merges'.format(self.repo_api_url)
        expected_response = self._get_expected_merge(conflict)
        status = 201
        if conflict:
            status = 409
        
        responses.add(
            method = responses.POST,
            url = api_url,
            json = expected_response,
            status = status,
        )
        return expected_response

    def _mock_pull_create(self, pull_id, issue_id):
        api_url = '{}/pulls'.format(self.repo_api_url)
        expected_response = self._get_expected_pull_request(pull_id, issue_id)
        
        responses.add(
            method = responses.POST,
            url = api_url,
            json = expected_response,
            status = 201,
        )

    def _mock_pulls(self, pulls=None):
        api_url = '{}/pulls'.format(self.repo_api_url)
        expected_response = self._get_expected_pulls(pulls=pulls)
        
        responses.add(
            method = responses.GET,
            url = api_url,
            json = expected_response,
        )

    def _mock_compare(self, base, head, files=None):
        api_url = '{}/compare/{}...{}'.format(self.repo_api_url, base, head)
        expected_response = self._get_expected_compare(base, head, files)
        
        responses.add(
            method = responses.GET,
            url = api_url,
            json = expected_response,
        )

    def _get_log_lines(self, log):
        log_lines = []
        for event in log.records:
            if event.name != 'cumulusci.core.tasks':
                continue
            log_lines.append((event.levelname, event.getMessage()))
        return log_lines
        

    @responses.activate
    def test_branch_does_not_exist(self):
        self._mock_repo()
        self._mock_branch_does_not_exist(self.branch)

        task = self._create_task()
        with self.assertRaises(GithubApiNotFoundError):
            task()
        self.assertEquals(len(responses.calls), 2)

    @responses.activate
    def test_no_feature_branch(self):
        self._mock_repo()
        self._mock_branch(self.branch)
        other_branch = self._get_expected_branch('not-a-feature-branch')
        self._mock_pulls()
        branches = [other_branch]
        branches = self._mock_branches(branches)
        with LogCapture() as l:
            task = self._create_task()
            task()
            log_lines = self._get_log_lines(l)
                    
            expected = [
                ('INFO', 'Beginning task: MergeBranch'),
                ('INFO', ''),
                ('DEBUG', 'Skipping branch master: is source branch'),
                ('DEBUG', 'Skipping branch not-a-feature-branch: does not match prefix feature/'),
            ]
            self.assertEquals(log_lines, expected)
        self.assertEquals(len(responses.calls), 4)

    @responses.activate
    def test_feature_branch_no_diff(self):
        self._mock_repo()
        self._mock_branch(self.branch)
        self._mock_pulls()
        branch_name = 'feature/a-test'
        branches = []
        branches.append(self._get_expected_branch(branch_name))
        branches = self._mock_branches(branches)
        self._mock_compare(
            base = branches[1]['name'],
            head = self.project_config.repo_commit,
        )
        with LogCapture() as l:
            task = self._create_task()
            task()
            log_lines = self._get_log_lines(l)

            expected = [
                ('INFO', 'Beginning task: MergeBranch'),
                ('INFO', ''),
                ('DEBUG', 'Skipping branch master: is source branch'),
                ('INFO', 'Skipping branch feature/a-test: no file diffs found'),
            ]
            self.assertEquals(log_lines, expected)
        self.assertEquals(len(responses.calls), 5)

    @responses.activate
    def test_feature_branch_merge(self):
        self._mock_repo()
        self._mock_branch(self.branch)
        self._mock_pulls()
        branch_name = 'feature/a-test'
        branches = []
        branches.append(self._get_expected_branch(branch_name))
        branches = self._mock_branches(branches)
        self._mock_compare(
            base = branches[1]['name'],
            head = self.project_config.repo_commit,
            files = [
                {'filename': 'test.txt'},
            ]
        )
        self._mock_merge()
        with LogCapture() as l:
            task = self._create_task()
            task()
            log_lines = self._get_log_lines(l)

            expected = [
                ('INFO', 'Beginning task: MergeBranch'),
                ('INFO', ''),
                ('DEBUG', 'Skipping branch master: is source branch'),
                ('INFO', 'Merged 1 commits into branch feature/a-test'),
            ]
            self.assertEquals(log_lines, expected)
        self.assertEquals(len(responses.calls), 6)
    
    @responses.activate
    def test_feature_branch_merge_conflict(self):
        self._mock_repo()
        self._mock_branch(self.branch)
        self._mock_pulls()
        branch_name = 'feature/a-test'
        branches = []
        branches.append(self._get_expected_branch(branch_name))
        branches = self._mock_branches(branches)
        self._mock_compare(
            base = branches[1]['name'],
            head = self.project_config.repo_commit,
            files = [
                {'filename': 'test.txt'},
            ]
        )
        self._mock_merge(True)
        self._mock_pull_create(1, 2)
        with LogCapture() as l:
            task = self._create_task()
            task()
            log_lines = self._get_log_lines(l)

            expected = [
                ('INFO', 'Beginning task: MergeBranch'),
                ('INFO', ''),
                ('DEBUG', 'Skipping branch master: is source branch'),
                ('INFO', 'Merge conflict on branch feature/a-test: created pull request #2')
            ]
            self.assertEquals(log_lines, expected)
        self.assertEquals(len(responses.calls), 7)

    @responses.activate
    def test_feature_branch_existing_pull(self):
        self._mock_repo()
        self._mock_branch(self.branch)

        branch_name = 'feature/a-test'
        branches = []
        branches.append(self._get_expected_branch(branch_name))
        branches = self._mock_branches(branches)

        pull = self._get_expected_pull_request(1, 2)
        pull['base']['ref'] = branch_name
        pull['base']['sha'] = branches[1]['commit']['sha']
        self._mock_pulls([pull])

        self._mock_compare(
            base = branches[1]['name'],
            head = self.project_config.repo_commit,
            files = [
                {'filename': 'test.txt'},
            ]
        )
        self._mock_merge(True)

        with LogCapture() as l:
            task = self._create_task()
            task()
            log_lines = self._get_log_lines(l)

            expected = [
                ('INFO', 'Beginning task: MergeBranch'),
                ('INFO', ''),
                ('DEBUG', 'Skipping branch master: is source branch'),
                ('INFO', 'Merge conflict on branch feature/a-test: merge PR already exists'),
            ]
            self.assertEquals(log_lines, expected)
        self.assertEquals(len(responses.calls), 6)


    @responses.activate
    def test_feature_branch_parent_child(self):
        self._mock_repo()
        self._mock_branch(self.branch)

        parent_branch_name = 'feature/a-test'
        child1_branch_name = 'feature/a-test__a-child1'
        child2_branch_name = 'feature/a-test__a-child2'
        branches = []
        branches.append(self._get_expected_branch(parent_branch_name))
        branches.append(self._get_expected_branch(child1_branch_name))
        branches.append(self._get_expected_branch(child2_branch_name))
        branches = self._mock_branches(branches)

        self._mock_pulls()

        self._mock_compare(
            base = branches[1]['name'],
            head = self.project_config.repo_commit,
            files = [
                {'filename': 'test.txt'},
            ]
        )
        merges = []
        merges.append(self._mock_merge())
        merges.append(self._mock_merge())
        merges.append(self._mock_merge())

        self._mock_compare(
            base = branches[2]['name'],
            head = merges[0]['sha'],
            files = [
                {'filename': 'test.txt'},
            ]
        )
        self._mock_compare(
            base = branches[3]['name'],
            head = merges[0]['sha'],
            files = [
                {'filename': 'test.txt'},
            ]
        )

        with LogCapture() as l:
            task = self._create_task()
            task()

            log_lines = self._get_log_lines(l)

            expected = [
                ('INFO', 'Beginning task: MergeBranch'),
                ('INFO', ''),
                ('DEBUG', 'Skipping branch master: is source branch'),
                ('INFO', 'Merged 1 commits into parent branch {}'.format(
                    parent_branch_name
                )),
                ('INFO', '  Merging into child branches:'),
                ('INFO', '    Merged 1 commits into branch {}'.format(
                    child1_branch_name
                )),
                ('INFO', '    Merged 1 commits into branch {}'.format(
                    child2_branch_name
                )),
            ]
            self.assertEquals(log_lines, expected)
        self.assertEquals(len(responses.calls), 10)
