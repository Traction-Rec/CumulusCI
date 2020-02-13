from pathlib import Path
from unittest import mock
import tempfile

from lxml import etree
import pytest

from cumulusci.core.exceptions import CumulusCIException
from cumulusci.tasks.salesforce.tests.util import create_task
from cumulusci.tasks.salesforce.metadata_etl import (
    BaseMetadataETLTask,
    BaseMetadataSynthesisTask,
    BaseMetadataTransformTask,
    MetadataSingleEntityTransformTask,
    get_new_tag_index,
)


class TestBaseMetadataETLTask:
    def test_init_options(self):
        task = create_task(
            BaseMetadataETLTask, {"unmanaged": True, "api_version": "47.0"}
        )

        assert task.options["unmanaged"]
        assert task.options["api_version"] == "47.0"

    def test_inject_namespace(self):
        task = create_task(
            BaseMetadataETLTask,
            {"unmanaged": False, "namespace_inject": "test", "api_version": "47.0"},
        )

        assert task._inject_namespace("%%%NAMESPACE%%%Test__c") == "test__Test__c"
        task.options["unmanaged"] = True
        assert task._inject_namespace("%%%NAMESPACE%%%Test__c") == "Test__c"

    @mock.patch("cumulusci.tasks.salesforce.metadata_etl.base.ApiRetrieveUnpackaged")
    def test_retrieve(self, api_mock):
        task = create_task(
            BaseMetadataETLTask,
            {"unmanaged": False, "namespace_inject": "test", "api_version": "47.0"},
        )
        task.retrieve_dir = mock.Mock()
        task._get_package_xml_content = mock.Mock()
        task._get_package_xml_content.return_value = ""

        task._retrieve()
        api_mock.assert_called_once_with(
            task, task._generate_package_xml(False), "47.0"
        )
        api_mock.return_value.assert_called_once_with()
        api_mock.return_value.return_value.extractall.assert_called_once_with(
            task.retrieve_dir
        )

    @mock.patch("cumulusci.tasks.salesforce.metadata_etl.base.Deploy")
    def test_deploy(self, deploy_mock):
        with tempfile.TemporaryDirectory() as tmpdir:
            task = create_task(
                BaseMetadataETLTask,
                {"unmanaged": False, "namespace_inject": "test", "api_version": "47.0"},
            )
            task.deploy_dir = Path(tmpdir)
            task._generate_package_xml = mock.Mock()
            task._generate_package_xml.return_value = "test"
            result = task._deploy()
            assert (Path(tmpdir) / "package.xml").read_text() == "test"

            assert len(deploy_mock.call_args_list) == 1

            assert deploy_mock.call_args_list[0][0][0] == task.project_config
            assert deploy_mock.call_args_list[0][0][2] == task.org_config
            deploy_mock.return_value.assert_called_once_with()
            assert result == deploy_mock.return_value.return_value

    def test_run_task(self):
        task = create_task(
            BaseMetadataETLTask,
            {"unmanaged": False, "namespace_inject": "test", "api_version": "47.0"},
        )

        task._retrieve = mock.Mock()
        task._deploy = mock.Mock()
        task.retrieve = True
        task.deploy = True

        task()

        task._retrieve.assert_called_once_with()
        task._deploy.assert_called_once_with()


class TestBaseMetadataSynthesisTask:
    def test_synthesis(self):
        task = create_task(
            BaseMetadataSynthesisTask,
            {"unmanaged": False, "namespace_inject": "test", "api_version": "47.0"},
        )

        task._deploy = mock.Mock()
        task._synthesize()
        task._synthesize = mock.Mock()

        task()

        task._deploy.assert_called_once_with()
        task._synthesize.assert_called_once_with()

    @mock.patch("cumulusci.tasks.salesforce.metadata_etl.base.PackageXmlGenerator")
    def test_generate_package_xml(self, package_mock):
        task = create_task(
            BaseMetadataSynthesisTask,
            {"unmanaged": False, "namespace_inject": "test", "api_version": "47.0"},
        )
        task.deploy_dir = "test"

        result = task._generate_package_xml(True)
        package_mock.assert_called_once_with(str(task.deploy_dir), task.api_version)
        package_mock.return_value.assert_called_once_with()
        assert result == package_mock.return_value.return_value


class TestBaseMetadataTransformTask:
    def test_generate_package_xml(self):
        task = create_task(
            BaseMetadataTransformTask,
            {"unmanaged": False, "namespace_inject": "test", "api_version": "47.0"},
        )

        task._get_entities = mock.Mock()
        task._get_entities.return_value = {
            "CustomObject": ["Account", "Contact"],
            "ApexClass": ["Test"],
        }

        assert (
            task._generate_package_xml(False)
            == """<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
    <types>
        <members>Account</members>
        <members>Contact</members>
        <name>CustomObject</name>
    </types>
    <types>
        <members>Test</members>
        <name>ApexClass</name>
    </types>

    <version>47.0</version>
</Package>
"""
        )


class TestMetadataSingleEntityTransformTask:
    def test_init_options(self):
        task = create_task(MetadataSingleEntityTransformTask, {})
        task._init_options(
            {
                "unmanaged": False,
                "api_version": "47.0",
                "namespace_inject": "test",
                "api_names": "%%%NAMESPACE%%%bar,foo",
            }
        )
        assert task.api_names == ["test__bar", "foo"]

    def test_get_entities(self):
        task = create_task(
            MetadataSingleEntityTransformTask,
            {"unmanaged": False, "api_version": "47.0", "api_names": "bar,foo"},
        )

        assert task._get_entities() == {None: ["bar", "foo"]}

        task = create_task(
            MetadataSingleEntityTransformTask,
            {"unmanaged": False, "api_version": "47.0"},
        )

        assert task._get_entities() == {None: ["*"]}

    def test_transform(self):
        task = create_task(
            MetadataSingleEntityTransformTask,
            {"unmanaged": False, "api_version": "47.0", "api_names": "Test"},
        )

        task.entity = "CustomApplication"
        task._transform_entity = mock.Mock()

        input_xml = """<?xml version="1.0" encoding="UTF-8"?>
<CustomApplication xmlns="http://soap.sforce.com/2006/04/metadata">
</CustomApplication>"""

        with tempfile.TemporaryDirectory() as tmpdir:
            task._create_directories(tmpdir)

            test_path = task.retrieve_dir / "applications"
            test_path.mkdir()
            test_path = test_path / "Test.app"

            test_path.write_text(input_xml)

            task._transform()

            assert len(task._transform_entity.call_args_list) == 1

    def test_transform__bad_entity(self):
        task = create_task(
            MetadataSingleEntityTransformTask,
            {"unmanaged": False, "api_version": "47.0", "api_names": "bar,foo"},
        )

        task.entity = "Battlestar"

        with pytest.raises(CumulusCIException):
            task._transform()

    def test_transform__non_xml_entity(self):
        task = create_task(
            MetadataSingleEntityTransformTask,
            {"unmanaged": False, "api_version": "47.0", "api_names": "bar,foo"},
        )

        task.entity = "LightningComponentBundle"

        with pytest.raises(CumulusCIException):
            task._transform()

    def test_transform__missing_record(self):
        task = create_task(
            MetadataSingleEntityTransformTask,
            {"unmanaged": False, "api_version": "47.0", "api_names": "Test"},
        )

        task.entity = "CustomApplication"

        with tempfile.TemporaryDirectory() as tmpdir:
            task._create_directories(tmpdir)

            test_path = task.retrieve_dir / "applications"
            test_path.mkdir()

            with pytest.raises(CumulusCIException):
                task._transform()


class TestUtilities:
    XML_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<CustomApplication xmlns="http://soap.sforce.com/2006/04/metadata">
    <defaultLandingTab>standard-Account</defaultLandingTab>
    <description>Application</description>
    <label>Application</label>
    <tabs>standard-Account</tabs>
    <tabs>standard-Contact</tabs>
    <formFactors>Large</formFactors>
</CustomApplication>
"""

    def test_get_new_tag_index(self):
        root = etree.fromstring(self.XML_SAMPLE).getroottree()

        assert get_new_tag_index(root, "tabs") == 5
        assert get_new_tag_index(root, "relatedList") == 0
