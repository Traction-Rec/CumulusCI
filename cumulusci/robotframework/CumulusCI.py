import logging

from robot.api import logger
from robot.libraries.BuiltIn import BuiltIn
from simple_salesforce import Salesforce
from requests import Session

from cumulusci.cli.config import CliRuntime
from cumulusci.core.config import TaskConfig
from cumulusci.core.exceptions import TaskOptionsError
from cumulusci.core.tasks import CURRENT_TASK
from cumulusci.core.utils import import_global
from cumulusci.robotframework.utils import set_pdb_trace, PerfJSONConverter
from cumulusci.tasks.robotframework.robotframework import Robot


class CumulusCI(object):
    """ Library for accessing CumulusCI for the local git project

        This library allows Robot Framework tests to access credentials to a
        Salesforce org created by CumulusCI, including Scratch Orgs.  It also
        exposes the core logic of CumulusCI including interactions with the
        Salesforce API's and project specific configuration including custom
        and customized tasks and flows.

        Initialization requires a single argument, the org name for the target
        CumulusCI org.  If running your tests via cci's robot task (recommended),
        you can initialize the library in your tests taking advantage of the
        variable set by the robot task:
        | ``*** Settings ***``
        |
        | Library  cumulusci.robotframework.CumulusCI  ${ORG}

    """

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def __init__(self, org_name=None):
        if not org_name:
            org_name = "dev"
        self.org_name = org_name
        self._project_config = None
        self._org = None
        self._sf = None
        self._tooling = None
        # Turn off info logging of all http requests
        logging.getLogger("requests.packages.urllib3.connectionpool").setLevel(
            logging.WARN
        )
        if self.perf_listener:
            # we never uninstall because the objects we are hooking are
            # scoped to the lifetime of this task anyhow
            self._install_perf_hook()

    @property
    def robot_task(self):
        if CURRENT_TASK.stack and isinstance(CURRENT_TASK.stack[0], Robot):
            return CURRENT_TASK.stack[0]
        else:
            return None

    @property
    def project_config(self):
        if self._project_config is None:
            if self.robot_task:
                # If CumulusCI is running a task, use that task's config
                return self.robot_task.project_config
            else:
                logger.console("Initializing CumulusCI config\n")
                self._project_config = CliRuntime().project_config
        return self._project_config

    def set_project_config(self, project_config):
        logger.console("\n")
        self._project_config = project_config

    @property
    def keychain(self):
        return self.project_config.keychain

    @property
    def perf_listener(self):
        if self.robot_task:
            return self.robot_task.robot_perf_listener

    @property
    def org(self):
        if self._org is None:
            if self.robot_task:
                # If CumulusCI is running a task, use that task's org
                return self.robot_task.org_config
            else:
                self._org = self.keychain.get_org(self.org_name)
        return self._org

    @property
    def sf(self):
        if self._sf is None:
            self._sf = self._init_api()
        return self._sf

    @property
    def tooling(self):
        if self._tooling is None:
            self._tooling = self._init_api("tooling/")
        return self._tooling

    def set_login_url(self):
        """ Sets the LOGIN_URL variable in the suite scope which will
            automatically log into the target Salesforce org.

            Typically, this is run during Suite Setup
        """
        BuiltIn().set_suite_variable("${LOGIN_URL}", self.org.start_url)

    def get_org_info(self):
        """ Returns a dictionary of the org information for the current target
            Salesforce org
        """
        return self.org.config

    def login_url(self, org=None):
        """ Returns the login url which will automatically log into the target
            Salesforce org.  By default, the org_name passed to the library
            constructor is used but this can be overridden with the org option
            to log into a different org.
        """
        if org is None:
            org = self.org
        else:
            org = self.keychain.get_org(org)
        return org.start_url

    def get_namespace_prefix(self, package=None):
        """ Returns the namespace prefix (including __) for the specified package name.
        (Defaults to project__package__name_managed from the current project config.)

        Returns an empty string if the package is not installed as a managed package.
        """
        result = ""
        if package is None:
            package = self.project_config.project__package__name_managed
        packages = self.tooling.query(
            "SELECT SubscriberPackage.NamespacePrefix, SubscriberPackage.Name "
            "FROM InstalledSubscriberPackage"
        )
        match = [
            p for p in packages["records"] if p["SubscriberPackage"]["Name"] == package
        ]
        if match:
            result = match[0]["SubscriberPackage"]["NamespacePrefix"] + "__"
        return result

    def run_task(self, task_name, **options):
        """ Runs a named CumulusCI task for the current project with optional
            support for overriding task options via kwargs.

            Examples:
            | =Keyword= | =task_name= | =task_options=             | =comment=                        |
            | Run Task  | deploy      |                            | Run deploy with standard options |
            | Run Task  | deploy      | path=path/to/some/metadata | Run deploy with custom path      |
        """
        task_config = self.project_config.get_task(task_name)
        class_path = task_config.class_path
        logger.console("\n")
        task_class, task_config = self._init_task(class_path, options, task_config)
        return self._run_task(task_class, task_config)

    def run_task_class(self, class_path, **options):
        """ Runs a CumulusCI task class with task options via kwargs.

            Use this keyword to run logic from CumulusCI tasks which have not
            been configured in the project's cumulusci.yml file.  This is
            most useful in cases where a test needs to use task logic for
            logic unique to the test and thus not worth making into a named
            task for the project

            Examples:
            | =Keyword=      | =task_class=                     | =task_options=                            |
            | Run Task Class | cumulusci.task.utils.DownloadZip | url=http://test.com/test.zip dir=test_zip |
        """
        logger.console("\n")
        task_class, task_config = self._init_task(class_path, options, TaskConfig())
        return self._run_task(task_class, task_config)

    def _init_api(self, base_url=None):
        api_version = self.project_config.project__package__api_version

        session = Session()
        session.hooks = {"response": []}

        sf = Salesforce(
            instance=self.org.instance_url.replace("https://", ""),
            session_id=self.org.access_token,
            version=api_version,
            session=session,
        )
        if base_url is not None:
            sf.base_url += base_url
        return sf

    def _init_task(self, class_path, options, task_config):
        task_class = import_global(class_path)
        task_config = self._parse_task_options(options, task_class, task_config)
        return task_class, task_config

    def _parse_task_options(self, options, task_class, task_config):
        if "options" not in task_config.config:
            task_config.config["options"] = {}
        # Parse options and add to task config
        if options:
            for name, value in options.items():
                # Validate the option
                if name not in task_class.task_options:
                    raise TaskOptionsError(
                        'Option "{}" is not available for task {}'.format(
                            name, task_class
                        )
                    )

                # Override the option in the task config
                task_config.config["options"][name] = value

        return task_config

    def _run_task(self, task_class, task_config):
        task = task_class(self.project_config, task_config, org_config=self.org)

        task()
        return task.return_values

    def debug(self):
        """Pauses execution and enters the Python debugger."""
        set_pdb_trace()

    def _register_response_hook(self, hook):
        if hook not in self.sf.session.hooks["response"]:
            self.sf.session.hooks["response"].append(hook)

    def _response_callback(self, response, **kwargs):
        self._last_performance_metrics = {}
        if "perfmetrics" in response.headers.keys():
            metric_str = response.headers["perfmetrics"]
            metadata = {}
            metadata["url"] = response.request.url
            metadata["method"] = response.request.method
            self._last_performance_metrics["_meta"] = metadata
            include_raw = self.perf_listener.verbosity >= 2
            perfjson = PerfJSONConverter(metric_str)
            self._last_performance_metrics.update(
                perfjson.to_dict(include_raw=include_raw)
            )
            self.perf_listener.report(self._last_performance_metrics)
            # sometimes it is handy to log this stuff
            # BuiltIn().log(perfjson.to_log_message(metadata))

    def _install_perf_hook(self):
        # https://github.com/forcedotcom/idecore/blob/f107a6cb61ee38cd7f5b24fc9610893f24a33264/config/wsdls/src/main/resources/apex.wsdl#L239
        self.sf.session.headers["Sforce-Call-Options"] = "perfOption=MINIMUM"
        self._register_response_hook(self._response_callback)

    def get_performance_metrics(self):
        """Performance metrics keyword: Get the last recorded performance metrics"""
        return self._last_performance_metrics

    def create_duration_metric(self, name):
        """Custom metric keyword: Start measuring the time it takes to do something: EXPERIMENTAL"""
        self.perf_listener.create_duration_metric(name)

    def end_duration_metric(self, name):
        """Custom metric keyword: Finish measuring a duration: EXPERIMENTAL"""
        self.perf_listener.end_duration_metric(name)

    def create_aggregate_metric(self, name, aggregation):
        """Custom metric keyword: Start averaging/summing/... something: EXPERIMENTAL
            aggregation should be "average", or "sum"  """
        self.perf_listener.create_aggregate_metric(name, aggregation)

    def store_metric_value(self, name, value):
        """Custom metric keyword: Add this number to the average or sum. EXPERIMENTAL"""
        self.perf_listener.store_metric_value(name, value)
