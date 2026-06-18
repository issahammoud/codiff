"""Setup pipeline: parse a repository and write the call graph to the database."""

import logging
import os
import uuid
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from codiff.db import Base, Class, Function, Repository, get_db_path, make_sync_engine
from codiff.parsers import CodeParser
from codiff.resolvers import resolve_internal_calls
from codiff.utils.files import is_venv_dir
from codiff.utils.gitignore_utils import is_dir_ignored, load_gitignore

logger = logging.getLogger(__name__)


def build_modules_dict(repo_path: Path, parser: CodeParser, gitignore=None) -> dict:
    """Build mapping of file paths to module names."""
    modules_dict = {}
    init_modules = {}
    repo_str = str(repo_path)

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d
            for d in dirs
            if d not in parser.exclude_dirs
            and not is_venv_dir(root, d)
            and not is_dir_ignored(gitignore, repo_str, root, d)
        ]

        for file in files:
            if file.endswith(".py"):
                file_path = Path(root) / file
                relative_path = str(file_path.relative_to(repo_path))

                module_name = relative_path.replace("/", ".").replace(".py", "")
                is_init = file == "__init__.py"

                parts = module_name.split(".")
                for i in range(len(parts)):
                    for j in range(i + 1, len(parts) + 1):
                        sub_path = ".".join(parts[i:j])

                        if is_init:
                            init_modules[sub_path] = module_name
                        elif sub_path not in init_modules:
                            modules_dict[sub_path] = module_name

    modules_dict.update(init_modules)
    return modules_dict


def build_package_exports(repo_path: Path, parser: CodeParser, gitignore=None) -> dict:
    """Build mapping of package exports from __init__.py files."""
    import re

    package_exports = {}
    repo_str = str(repo_path)

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d
            for d in dirs
            if d not in parser.exclude_dirs
            and not is_venv_dir(root, d)
            and not is_dir_ignored(gitignore, repo_str, root, d)
        ]

        if "__init__.py" in files:
            init_path = Path(root) / "__init__.py"
            relative_dir = str(init_path.parent.relative_to(repo_path))

            if relative_dir == ".":
                package_name = ""
            else:
                package_name = relative_dir.replace("/", ".")

            try:
                with open(init_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                pattern = r"from\s+\.(\w+)\s+import\s+(?:\(([^)]+)\)|([^(\n]+))"
                for match in re.finditer(pattern, content, re.DOTALL):
                    submodule = match.group(1)
                    names_str = match.group(2) or match.group(3)

                    names_str = re.sub(r"#[^\n]*", "", names_str)

                    for name_part in names_str.split(","):
                        name_part = name_part.strip()
                        if not name_part:
                            continue

                        if " as " in name_part:
                            parts = name_part.split(" as ")
                            original_name = parts[0].strip()
                            alias = parts[1].strip()
                        else:
                            original_name = name_part.strip()
                            alias = original_name

                        if not original_name or not re.match(r"^[a-zA-Z_]\w*$", original_name):
                            continue
                        if not alias or not re.match(r"^[a-zA-Z_]\w*$", alias):
                            continue

                        if package_name:
                            export_key = f"{package_name}.{alias}"
                            real_path = f"{package_name}.{submodule}.{original_name}"
                        else:
                            export_key = alias
                            real_path = f"{submodule}.{original_name}"

                        package_exports[export_key] = real_path

            except Exception:
                pass

    return package_exports


def setup_repository(repo_path: str) -> str:
    """
    Parse a repository and write the call graph to a SQLite database at {repo_path}/.codiff.db.

    Args:
        repo_path: Path to the repository root directory.

    Returns:
        The repository UUID (as string).
    """
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path):
        raise FileNotFoundError(f"Repository path not found: {repo_path}")

    repo_name = os.path.basename(repo_path)
    db_path = get_db_path(repo_path)

    logger.info("Setting up repository: %s", repo_name)
    logger.info("Database: %s", db_path)

    engine = make_sync_engine(db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # Create repository record
        repo_id = str(uuid.uuid4())
        repository = Repository(
            id=repo_id,
            name=repo_name,
            url=repo_path,
        )
        db.add(repository)
        db.commit()

        # Parse
        logger.info("Parsing code...")
        parser = CodeParser()
        repo_path_obj = Path(repo_path)
        gitignore = load_gitignore(repo_path)

        modules_dict = build_modules_dict(repo_path_obj, parser, gitignore)
        package_exports = build_package_exports(repo_path_obj, parser, gitignore)

        functions_list = []
        classes_list = []
        imports_dict = {}
        module_docstrings = {}
        class_docstrings = {}

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [
                d
                for d in dirs
                if d not in parser.exclude_dirs
                and not is_venv_dir(root, d)
                and not is_dir_ignored(gitignore, repo_path, root, d)
            ]

            for file in files:
                if file.endswith(".py"):
                    file_path = Path(root) / file
                    relative_path = str(file_path.relative_to(repo_path_obj))

                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            source_code = f.read()

                        functions, classes, imports, module_docstring = parser.parse_code(
                            source_code, relative_path, modules_dict
                        )

                        if module_docstring:
                            module_docstrings[relative_path] = module_docstring

                        functions_list.extend(functions)
                        classes_list.extend(classes)
                        imports_dict.update(imports)

                        for cls in classes:
                            if cls.docstring:
                                class_docstrings[cls.name] = cls.docstring

                    except Exception as e:
                        logger.warning("Error parsing %s: %s", file_path, e)
                        continue

        logger.info("Parsed %d functions, %d classes", len(functions_list), len(classes_list))

        # Resolve internal calls
        logger.info("Resolving internal calls...")
        functions_list = resolve_internal_calls(
            functions=functions_list,
            classes=classes_list,
            imports=imports_dict,
            modules_dict=modules_dict,
            package_exports=package_exports,
            max_workers=4,
        )

        # Store functions
        logger.info("Storing functions...")
        for func_chunk in functions_list:
            module_doc = module_docstrings.get(func_chunk.file_path)
            class_doc = (
                class_docstrings.get(func_chunk.class_name) if func_chunk.class_name else None
            )

            function = Function(
                repository_id=repo_id,
                function_id=func_chunk.id,
                name=func_chunk.name,
                file_path=func_chunk.file_path,
                class_name=func_chunk.class_name,
                nested=func_chunk.nested,
                code=func_chunk.code,
                docstring=func_chunk.docstring,
                module_docstring=module_doc,
                class_docstring=class_doc,
                start_line=func_chunk.start_line,
                end_line=func_chunk.end_line,
                parameters=[p.to_dict() for p in func_chunk.parameters]
                if func_chunk.parameters
                else None,
                decorators=func_chunk.decorators,
                return_type=func_chunk.return_type,
                calls=func_chunk.calls,
            )
            db.add(function)

        # Store classes
        logger.info("Storing classes...")
        for cls_chunk in classes_list:
            cls = Class(
                repository_id=repo_id,
                class_id=cls_chunk.id,
                name=cls_chunk.name,
                file_path=cls_chunk.file_path,
                code=cls_chunk.code,
                docstring=cls_chunk.docstring,
                start_line=cls_chunk.start_line,
                end_line=cls_chunk.end_line,
                decorators=cls_chunk.decorators,
                superclasses=cls_chunk.superclasses,
            )
            db.add(cls)

        repository.total_functions = len(functions_list)
        repository.total_classes = len(classes_list)
        repository.is_parsed = True
        db.commit()

        logger.info("Setup complete! Repository ID: %s", repo_id)
        return repo_id

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
