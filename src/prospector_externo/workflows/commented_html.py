"""Workflow HTML que además inspecciona enlaces presentes en comentarios HTML."""

from typing import List, Set
from bs4 import BeautifulSoup, Comment

from prospector_externo.domain.models import DiscoveryType, ResourceCandidate, SourceConfig
from prospector_externo.workflows.base import ResourceDetector
from prospector_externo.workflows.html_workflow import HtmlWorkflow


class CommentedHtmlWorkflow(HtmlWorkflow):
    discovery_type = DiscoveryType.COMMENTED_HTML

    def _extract_from_comments(
        self,
        html_content: str,
        current_url: str,
        config: SourceConfig,
        seen_resource_keys: Set[str],
    ) -> List[ResourceCandidate]:
        soup = BeautifulSoup(html_content, "html.parser")
        resources: List[ResourceCandidate] = []
        comments = soup.find_all(string=lambda text: isinstance(text, Comment))

        for comment in comments:
            comment_soup = BeautifulSoup(str(comment), "html.parser")
            for tag in comment_soup.find_all("a", href=True):
                raw_href = tag["href"].strip()
                if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                from prospector_externo.domain.normalizer import UrlNormalizer
                normalized = UrlNormalizer.normalize(raw_href, base_url=current_url)
                if not ResourceDetector.is_resource(normalized):
                    continue
                title = tag.get_text(" ", strip=True)
                resource = self._make_resource(
                    config=config,
                    raw_url=raw_href,
                    current_url=current_url,
                    title=title,
                    discovery_type=DiscoveryType.COMMENTED_HTML,
                    anchor_text=title,
                )
                if resource.resource_key in seen_resource_keys:
                    continue
                seen_resource_keys.add(resource.resource_key)
                resources.append(resource)
        return resources

    def _extract_resources_and_links(self, html_content, current_url, config, *,
                                     discovery_type, seen_resource_keys=None):
        seen = seen_resource_keys if seen_resource_keys is not None else set()
        resources, links = super()._extract_resources_and_links(
            html_content, current_url, config, discovery_type=self.discovery_type,
            seen_resource_keys=seen,
        )
        resources.extend(self._extract_from_comments(html_content, current_url, config, seen))
        return resources, links
