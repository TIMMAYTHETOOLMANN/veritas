"""
SkillIndexer — Auto-discovers and indexes all available skills from Hermes and project directories.
"""
import os
import json
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import re


@dataclass
class SkillMetadata:
    """Metadata extracted from a skill's SKILL.md frontmatter."""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    license: str = ""
    platforms: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    related_skills: List[str] = field(default_factory=list)
    trigger_keywords: List[str] = field(default_factory=list)
    category: str = ""
    path: str = ""
    last_indexed: str = ""
    raw_frontmatter: Dict[str, Any] = field(default_factory=dict)


class SkillIndexer:
    """
    Discovers, parses, and indexes all skills from:
    1. Hermes user skills directory (~/.hermes/skills/)
    2. Project-local skills (./skills/, ./dacs/skills/)
    3. Plugin-provided skills
    
    Builds a searchable index with trigger keywords for intelligent allocation.
    """
    
    def __init__(self, 
                 hermes_skills_path: Optional[str] = None,
                 project_skills_path: Optional[str] = None,
                 extra_paths: Optional[List[str]] = None):
        self.hermes_skills_path = hermes_skills_path or os.path.expanduser("~/.hermes/skills")
        self.project_skills_path = project_skills_path or os.path.join(os.getcwd(), "skills")
        self.extra_paths = extra_paths or []
        
        self.index: Dict[str, SkillMetadata] = {}
        self.keyword_index: Dict[str, List[str]] = {}  # keyword -> [skill_names]
        self.category_index: Dict[str, List[str]] = {}  # category -> [skill_names]
        self._indexed_at: Optional[datetime] = None
    
    def discover_all(self, force_refresh: bool = False) -> Dict[str, SkillMetadata]:
        """Discover and index all skills from all configured paths."""
        if self._indexed_at and not force_refresh:
            return self.index
        
        self.index = {}
        self.keyword_index = {}
        self.category_index = {}
        
        search_paths = [
            self.hermes_skills_path,
            self.project_skills_path,
        ] + self.extra_paths
        
        for base_path in search_paths:
            self._scan_directory(base_path)
        
        self._build_keyword_index()
        self._indexed_at = datetime.now()
        return self.index
    
    def _scan_directory(self, base_path: str) -> None:
        """Recursively scan a directory for skill folders with SKILL.md files."""
        base = Path(base_path)
        if not base.exists():
            return
        
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                # Check for nested skills (category subdirectories)
                for sub_dir in skill_dir.iterdir():
                    if sub_dir.is_dir():
                        sub_skill_file = sub_dir / "SKILL.md"
                        if sub_skill_file.exists():
                            self._parse_skill(sub_skill_file, sub_dir.name)
                continue
            
            self._parse_skill(skill_file, skill_dir.name)
    
    def _parse_skill(self, skill_file: Path, skill_name: str) -> None:
        """Parse a SKILL.md file and extract metadata."""
        try:
            content = skill_file.read_text(encoding='utf-8')
            
            # Extract YAML frontmatter
            frontmatter = {}
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1]) or {}
            
            # Extract trigger keywords from description and tags
            description = frontmatter.get('description', '')
            tags = frontmatter.get('tags', [])
            related = frontmatter.get('related_skills', [])
            
            # Auto-generate trigger keywords from description
            trigger_keywords = self._extract_keywords(description, tags)
            
            metadata = SkillMetadata(
                name=frontmatter.get('name', skill_name),
                description=description,
                version=frontmatter.get('version', '1.0.0'),
                author=frontmatter.get('author', ''),
                license=frontmatter.get('license', ''),
                platforms=frontmatter.get('platforms', []),
                tags=tags,
                related_skills=related,
                trigger_keywords=trigger_keywords,
                category=frontmatter.get('category', self._infer_category(skill_file)),
                path=str(skill_file.parent),
                last_indexed=datetime.now().isoformat(),
                raw_frontmatter=frontmatter,
            )
            
            self.index[metadata.name] = metadata
            
        except Exception as e:
            print(f"Warning: Failed to parse skill {skill_file}: {e}")
    
    def _extract_keywords(self, description: str, tags: List[str]) -> List[str]:
        """Extract searchable keywords from description and tags."""
        keywords = set()
        
        # Add tags directly
        keywords.update(tags)
        
        # Extract meaningful words from description
        if description:
            # Remove common words, keep technical terms
            words = re.findall(r'\b[a-zA-Z]{3,}\b', description.lower())
            stop_words = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'when', 'use', 'using', 
                         'skill', 'tool', 'agent', 'system', 'using', 'build', 'create', 'make', 'run'}
            keywords.update(w for w in words if w not in stop_words and len(w) > 3)
        
        return list(keywords)
    
    def _infer_category(self, skill_file: Path) -> str:
        """Infer category from directory structure."""
        parts = skill_file.parts
        # Look for known category names in path
        categories = ['blockchain', 'security', 'mlops', 'devops', 'research', 'creative', 
                     'finance', 'productivity', 'communication', 'data-science', 'web-development',
                     'software-development', 'offensive-security', 'autonomous-ai-agents',
                     'autonomous-systems', 'migration', 'gaming', 'health', 'note-taking',
                     'smart-home', 'nudge', 'self-healing', 'mainnet-token-launch']
        
        for part in parts:
            if part in categories:
                return part
        
        # Default to parent directory name
        return skill_file.parent.name
    
    def _build_keyword_index(self) -> None:
        """Build inverted index for fast keyword-based lookup."""
        for skill_name, metadata in self.index.items():
            # Index by trigger keywords
            for keyword in metadata.trigger_keywords:
                self.keyword_index.setdefault(keyword.lower(), []).append(skill_name)
            
            # Index by category
            if metadata.category:
                self.category_index.setdefault(metadata.category, []).append(skill_name)
            
            # Index by tags
            for tag in metadata.tags:
                self.keyword_index.setdefault(tag.lower(), []).append(skill_name)
    
    def find_by_keywords(self, keywords: List[str], min_matches: int = 1) -> List[SkillMetadata]:
        """Find skills matching the given keywords, ranked by match count."""
        scores: Dict[str, int] = {}
        
        for kw in keywords:
            kw_lower = kw.lower()
            # Exact match
            if kw_lower in self.keyword_index:
                for skill in self.keyword_index[kw_lower]:
                    scores[skill] = scores.get(skill, 0) + 2
            
            # Partial/fuzzy match
            for indexed_kw, skills in self.keyword_index.items():
                if kw_lower in indexed_kw or indexed_kw in kw_lower:
                    for skill in skills:
                        scores[skill] = scores.get(skill, 0) + 1
        
        # Filter by min_matches and sort by score
        results = [
            (skill, score) for skill, score in scores.items() 
            if score >= min_matches
        ]
        results.sort(key=lambda x: x[1], reverse=True)
        
        return [self.index[skill] for skill, _ in results]
    
    def find_by_category(self, category: str) -> List[SkillMetadata]:
        """Find all skills in a category."""
        skill_names = self.category_index.get(category, [])
        return [self.index[name] for name in skill_names if name in self.index]
    
    def find_related(self, skill_name: str) -> List[SkillMetadata]:
        """Find skills related to the given skill."""
        if skill_name not in self.index:
            return []
        
        related = self.index[skill_name].related_skills
        results = [self.index[name] for name in related if name in self.index]
        
        # Also include skills sharing keywords
        keywords = self.index[skill_name].trigger_keywords
        keyword_matches = self.find_by_keywords(keywords, min_matches=2)
        
        # Merge and deduplicate
        seen = set()
        for skill in results + keyword_matches:
            if skill.name not in seen:
                seen.add(skill.name)
                yield skill
    
    def get_skill(self, name: str) -> Optional[SkillMetadata]:
        """Get skill metadata by name."""
        return self.index.get(name)
    
    def get_all_skills(self) -> List[SkillMetadata]:
        """Get all indexed skills."""
        return list(self.index.values())
    
    def export_index(self, path: str) -> None:
        """Export index to JSON for caching."""
        data = {
            'indexed_at': self._indexed_at.isoformat() if self._indexed_at else None,
            'skills': {
                name: {
                    'name': meta.name,
                    'description': meta.description,
                    'version': meta.version,
                    'category': meta.category,
                    'tags': meta.tags,
                    'trigger_keywords': meta.trigger_keywords,
                    'related_skills': meta.related_skills,
                    'path': meta.path,
                }
                for name, meta in self.index.items()
            }
        }
        Path(path).write_text(json.dumps(data, indent=2))
    
    def load_index(self, path: str) -> bool:
        """Load index from JSON cache."""
        try:
            data = json.loads(Path(path).read_text())
            self._indexed_at = datetime.fromisoformat(data['indexed_at']) if data['indexed_at'] else None
            
            for name, meta_data in data['skills'].items():
                meta = SkillMetadata(**meta_data)
                self.index[name] = meta
            
            self._build_keyword_index()
            return True
        except Exception:
            return False


# Convenience function for quick access
_default_indexer: Optional[SkillIndexer] = None

def get_indexer() -> SkillIndexer:
    """Get or create the default skill indexer."""
    global _default_indexer
    if _default_indexer is None:
        _default_indexer = SkillIndexer()
    return _default_indexer

def discover_skills(force: bool = False) -> Dict[str, SkillMetadata]:
    """Quick function to discover all skills."""
    return get_indexer().discover_all(force_refresh=force)

def find_skills_for_task(task_description: str) -> List[SkillMetadata]:
    """Find skills relevant to a task description."""
    indexer = get_indexer()
    if not indexer.index:
        indexer.discover_all()
    
    # Extract keywords from task description
    keywords = re.findall(r'\b[a-zA-Z]{3,}\b', task_description.lower())
    stop_words = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'when', 'use', 'using', 
                 'skill', 'tool', 'agent', 'system', 'using', 'build', 'create', 'make', 'run',
                 'need', 'want', 'please', 'help', 'how', 'what', 'can', 'you', 'me'}
    keywords = [k for k in keywords if k not in stop_words]
    
    return indexer.find_by_keywords(keywords)