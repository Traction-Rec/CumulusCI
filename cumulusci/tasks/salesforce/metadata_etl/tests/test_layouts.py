from lxml import etree

from cumulusci.tasks.salesforce.tests.util import create_task
from cumulusci.tasks.salesforce.metadata_etl import AddRelatedLists
from cumulusci.tasks.salesforce.metadata_etl import MD

LAYOUT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Layout xmlns="http://soap.sforce.com/2006/04/metadata">
    <layoutSections>
        <customLabel>false</customLabel>
        <detailHeading>false</detailHeading>
        <editHeading>true</editHeading>
        <label>Information</label>
        <layoutColumns>
            <layoutItems>
                <behavior>Readonly</behavior>
                <field>Name</field>
            </layoutItems>
        </layoutColumns>
        <layoutColumns/>
        <style>TwoColumnsTopToBottom</style>
    </layoutSections>
    {relatedLists}
</Layout>
"""

RELATED_LIST = """    <relatedLists>
        <fields>FULL_NAME</fields>
        <fields>CONTACT.TITLE</fields>
        <fields>CONTACT.EMAIL</fields>
        <fields>CONTACT.PHONE1</fields>
        <relatedList>RelatedContactList</relatedList>
    </relatedLists>
"""


class TestAddRelatedLists:
    def test_adds_related_list(self):
        task = create_task(
            AddRelatedLists,
            {
                "unmanaged": False,
                "api_version": "47.0",
                "api_names": "bar,foo",
                "related_list": "TEST",
                "fields": "foo__c,bar__c",
            },
        )

        tree = etree.fromstring(
            LAYOUT_XML.format(relatedLists=RELATED_LIST).encode("utf-8")
        ).getroottree()

        assert len(tree.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']")) == 0

        result = task._transform_entity(tree, "Layout")

        assert len(result.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']")) == 1
        field_elements = result.findall(
            f".//{MD}relatedLists[{MD}relatedList='TEST']/{MD}fields"
        )
        field_names = {elem.text for elem in field_elements}
        assert field_names == set(["foo__c", "bar__c"])

    def test_adds_related_list_no_existing(self):
        task = create_task(
            AddRelatedLists,
            {
                "unmanaged": False,
                "api_version": "47.0",
                "api_names": "bar,foo",
                "related_list": "TEST",
                "fields": "foo__c,bar__c",
            },
        )

        tree = etree.fromstring(
            LAYOUT_XML.format(relatedLists="").encode("utf-8")
        ).getroottree()

        assert len(tree.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']")) == 0

        result = task._transform_entity(tree, "Layout")

        assert len(result.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']")) == 1
        field_elements = result.findall(
            f".//{MD}relatedLists[{MD}relatedList='TEST']/{MD}fields"
        )
        field_names = {elem.text for elem in field_elements}
        assert field_names == set(["foo__c", "bar__c"])

    def test_skips_existing_related_list(self):
        task = create_task(
            AddRelatedLists,
            {
                "unmanaged": False,
                "api_version": "47.0",
                "api_names": "bar,foo",
                "related_list": "RelatedContactList",
                "fields": "foo__c,bar__c",
            },
        )

        tree = etree.fromstring(
            LAYOUT_XML.format(relatedLists=RELATED_LIST).encode("utf-8")
        ).getroottree()

        assert (
            len(
                tree.findall(
                    f".//{MD}relatedLists[{MD}relatedList='RelatedContactList']"
                )
            )
            == 1
        )

        result = task._transform_entity(tree, "Layout")

        assert (
            len(
                result.findall(
                    f".//{MD}relatedLists[{MD}relatedList='RelatedContactList']"
                )
            )
            == 1
        )
