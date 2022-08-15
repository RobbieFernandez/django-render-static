# pylint: disable=C0114

import os
from collections import Counter, namedtuple
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional, Union

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.template.backends.django import Template as DjangoTemplate
from django.template.exceptions import TemplateDoesNotExist
from django.template.utils import InvalidTemplateEngineError
from django.utils.functional import cached_property
from django.utils.module_loading import import_string
from render_static import Jinja2DependencyNeeded
from render_static.backends import StaticDjangoTemplates, StaticJinja2Templates
from render_static.context import resolve_context
from render_static.exceptions import InvalidContext

try:
    # pylint: disable=C0412
    from django.template.backends.jinja2 import Template as Jinja2Template
except ImportError:  # pragma: no cover
    Jinja2Template = Jinja2DependencyNeeded

__all__ = ['StaticTemplateEngine', 'Render']


class Render(
    namedtuple('_Render', ['selector', 'config', 'template', 'destination'])
):
    """
    A named tuple that holds all the pertinent information for a template
    including:

        - The selector used to select it
        - Its configuration from settings, if any
        - Its template engine Template class - could be a Django or Jinja2
            template
        - The destination where it will be/was rendered
    """
    def __str__(self) -> str:
        app = getattr(self.template.origin, 'app', None)
        if app:
            return f'[{app.label}] {self.template.origin.template_name} -> ' \
                   f'{self.destination}'
        return f'{self.template.origin.template_name} -> {self.destination}'


def _resolve_context(
        context: Optional[Union[Dict, Callable, str, Path]],
        template: Optional[str] = None
) -> Dict:
    """
    Resolve a context configuration parameter into a context dictionary. If
    the context is a string it is treated as an importable string pointing to
    a callable, if it is a callable it is called and if it is a dictionary it
    is simply returned. Any failure to resolve a dictionary from the
    configuration.

    :param context: Either an importable string pointing to a callable, a
        callable instance or a dictionary
    :return: dictionary holding the context
    :raises ImproperlyConfigured: if there is a failure to produce a
        dictionary context
    """
    try:
        return resolve_context(context)
    except InvalidContext as inval_ctx:
        raise ImproperlyConfigured(
            f"STATIC_TEMPLATES 'context' configuration directive"
            f"{' for ' + template if template else '' } must be a dictionary "
            f"or a callable that returns a dictionary!"
        ) from inval_ctx


class StaticTemplateEngine:
    """
    An engine for rendering static templates to disk based on a standard
    ``STATIC_TEMPLATES`` configuration either passed in at construction or
    obtained from settings. Static templates are most usually generated by a
    run of :ref:`renderstatic` preceding `collectstatic`, but this class
    encapsulates all the behavior of the static engine, may be used
    independently and can override configured parameters including contexts
    and render destinations:

    .. code-block::

        from render_static.engine import StaticTemplateEngine
        from django.conf import settings
        from pathlib import Path

        # This engine uses the settings.STATIC_TEMPLATE config
        engine = StaticTemplateEngine()

        # This engine uses a custom configuration
        engine = StaticTemplateEngine({
            'ENGINES': [{
                'BACKEND': 'render_static.backends.StaticJinja2Templates',
                'APP_DIRS': True
            }],
            'context': {
                'var1': 'value1'
            },
            'templates': {
                'app/html/my_template.html': {
                    'context': {
                        'var1': 'value2'
                    }
                }
            }
        })

        # this will render the my_template.html template to
        # app/static/app/html/my_template.html with the context:
        # { 'settings': settings, 'var1': 'value2' }
        engine.render_to_disk('app/html/my_template.html')

        # using the engine directly we can override configuration directives,
        # this will render the template with the context:
        # { 'settings': settings, 'var1': 'value3' } @ the custom location
        # 'static_dir/rendered.html'
        engine.render_to_disk(
            'app/html/my_template.html',
            context={'var1': 'value3'},
            destination=Path(settings.BASE_DIR) / 'static_dir/rendered.html'
        )


    :param config: If provided use this configuration instead of the one from
        settings
    :raises ImproperlyConfigured: If there are any errors in the configuration
        passed in or specified in settings.
    """

    config_: Dict = {}

    DEFAULT_ENGINE_CONFIG = [{
        'BACKEND': 'render_static.backends.StaticDjangoTemplates',
        'OPTIONS': {
            'loaders': ['render_static.loaders.StaticAppDirectoriesBatchLoader'],
            'builtins': ['render_static.templatetags.render_static']
        },
    }]

    class TemplateConfig:
        """
        Container for template specific configuration parameters.

        :param name: The name of the template
        :param dest: The absolute destination directory where the template will
            be written. May be
            None which indicates the template will be written to its owning
            app's static directory if it was loaded with an app directory
            loader
        :param context: A specific dictionary context to use for this template,
            may also be an import string to a callable or a callable that
            generates a dictionary. This may override global context parameters.
        :raises ImproperlyConfigured: If there are any unexpected or
            misconfigured parameters
        """

        context_: Dict = {}
        dest_: Optional[Path] = None

        def __init__(
                self,
                name: str,
                dest: Optional[Union[Path, str]] = None,
                context: Optional[Union[Dict, Callable, str]] = None
        ) -> None:
            self.name = name

            if dest is not None:
                if not isinstance(dest, (str, Path)):
                    raise ImproperlyConfigured(
                        f"Template {name} 'dest' parameter in STATIC_TEMPLATES"
                        f" must be a string or path-like object, not "
                        f"{type(dest)}"
                    )
                self.dest_ = Path(dest)
                if not self.dest_.is_absolute():
                    raise ImproperlyConfigured(
                        f'In STATIC_TEMPLATES, template {name} dest must be '
                        f'absolute!'
                    )
            context = _resolve_context(context, template=name)
            if context:
                self.context_ = context

        @property
        def context(self) -> Dict:
            """
            The context specific to this template. This will not include global
            parameters only the context as specified in the template
            configuration.
            """
            return self.context_

        @property
        def dest(self) -> Optional[Path]:
            """
            The location this template should be saved to, if specified.
            """
            return self.dest_

    def __init__(self, config: Optional[Dict] = None) -> None:
        if config:
            self.config_ = config

    @cached_property
    def config(self) -> dict:
        """
        Lazy configuration property. Fetch the ``STATIC_TEMPLATES``
        configuration dictionary which will either be the configuration passed
        in on initialization or the config specified in the
        ``STATIC_TEMPLATES`` setting.

        :return: The ``STATIC_TEMPLATES`` configuration this engine has
            initialized from
        :raises ImproperlyConfigured: If there are any terminal errors with the
            configurations
        """
        if not self.config_:
            if not hasattr(settings, 'STATIC_TEMPLATES'):
                raise ImproperlyConfigured(
                    'No STATIC_TEMPLATES configuration directive in settings!'
                )

            self.config_ = settings.STATIC_TEMPLATES if \
                settings.STATIC_TEMPLATES is not None else {}

        unrecognized_keys = [
            key for key in self.config_.keys() if key not in [
                'ENGINES', 'templates', 'context'
            ]
        ]
        if unrecognized_keys:
            raise ImproperlyConfigured(
                f'Unrecognized STATIC_TEMPLATES configuration directives: '
                f'{unrecognized_keys}'
            )
        return self.config_

    @cached_property
    def context(self) -> dict:
        """
        Lazy context property. Fetch the global context that will be fed to all
        templates. This includes the settings object and anything listed in the
        context dictionary in the ``STATIC_TEMPLATES`` configuration.

        :return: A dictionary containing the global template context
        :raises ImproperlyConfigured: If the template context is specified and
            is not a dictionary.
        """
        return {
            'settings': settings,
            **_resolve_context(self.config.get('context', {}))
        }

    @cached_property
    def templates(self) -> dict:
        """
        Lazy template property Fetch the dictionary mapping template names to
        TemplateConfig objects initializing them if necessary.

        :return: A dictionary mapping template names to configurations
        :raise ImproperlyConfigured: If there are any configuration issues with
            the templates
        """
        try:
            templates = {
                name: StaticTemplateEngine.TemplateConfig(name=name, **config)
                for name, config in self.config.get('templates', {}).items()
            }
        except ImproperlyConfigured:
            raise
        except Exception as exp:
            raise ImproperlyConfigured(
                f"Invalid 'templates' in STATIC_TEMPLATE: {exp}!"
            ) from exp

        return templates

    @cached_property
    def engines(self) -> dict:
        """
        Lazy engines property. Fetch the dictionary of engine names to engine
        instances based on the configuration, initializing said entities if
        necessary.

        :return: A dictionary mapping engine names to instances
        :raise ImproperlyConfigured: If there are configuration problems with
            the engine backends.
        """
        engine_defs = self.config.get('ENGINES', None)
        if engine_defs is None:
            self.config['ENGINES'] = self.DEFAULT_ENGINE_CONFIG
        elif not hasattr(engine_defs, '__iter__'):
            raise ImproperlyConfigured(
                f'ENGINES in STATIC_TEMPLATES setting must be an iterable '
                f'containing engine configurations! Encountered: '
                f'{type(engine_defs)}'
            )

        engines = {}
        backend_names = []
        for backend in self.config.get('ENGINES', []):
            try:
                # This will raise an exception if 'BACKEND' doesn't exist or
                # isn't a string containing at least one dot.
                default_name = backend['BACKEND'].rsplit('.', 2)[-1]
            except Exception as exp:
                invalid_backend = backend.get('BACKEND', '<not defined>')
                raise ImproperlyConfigured(
                    f'Invalid BACKEND for a static template engine: '
                    f'{invalid_backend}. Check your STATIC_TEMPLATES setting.'
                ) from exp

            # set defaults
            backend = {
                'NAME': default_name,
                'DIRS': [],
                'APP_DIRS': False,
                'OPTIONS': {},
                **backend,
            }
            engines[backend['NAME']] = backend
            backend_names.append(backend['NAME'])

        counts = Counter(backend_names)
        duplicates = [
            alias for alias, count in counts.most_common() if count > 1
        ]
        if duplicates:
            raise ImproperlyConfigured(
                f"Template engine aliases are not unique, duplicates: "
                f"{', '.join(duplicates)}. Set a unique NAME for each engine "
                f"in settings.STATIC_TEMPLATES."
            )

        for alias, config in engines.items():
            params = config.copy()
            backend = params.pop('BACKEND')
            engines[alias] = import_string(backend)(params)

        return engines

    def __getitem__(
            self,
            alias: str
    ) -> Union[StaticDjangoTemplates, StaticJinja2Templates]:
        """
        Accessor for backend instances indexed by name.

        :param alias: The name of the backend to fetch
        :return: The backend instance
        :raises InvalidTemplateEngineError: If a backend of the given alias
            does not exist
        """
        try:
            return self.engines[alias]
        except KeyError as key_error:
            raise InvalidTemplateEngineError(
                f"Could not find config for '{alias}' "
                f"in settings.STATIC_TEMPLATES"
            ) from key_error

    def __iter__(self):
        """
        Iterate through the backends.
        """
        return iter(self.engines)

    def all(self) -> List[Union[StaticDjangoTemplates, StaticJinja2Templates]]:
        """
        Get a list of all registered engines in order of precedence.
        :return: A list of engine instances in order of precedence
        """
        return [self[alias] for alias in self]

    @staticmethod
    def resolve_destination(
            config: TemplateConfig,
            template: Union[Jinja2Template, DjangoTemplate],
            batch: bool,
            dest: Optional[Union[str, Path]] = None
    ) -> Path:
        """
        Resolve the destination for a template, given all present configuration
        parameters for it and arguments passed in.

        :param config: The template configuration
        :param template: The template object created by the backend, could be a
            Jinja2 or Django template
        :param batch: True if this is part of a batch render, false otherwise
        :param dest: The destination passed in from the command line
        :return: An absolute destination path
        :raises ImproperlyConfigured: if a render destination cannot be
            determined
        """
        app = getattr(template.origin, 'app', None)

        if dest is None:
            dest = config.dest

        if dest is None:
            if app:
                dest = Path(app.path) / 'static'
            else:
                try:
                    dest = Path(settings.STATIC_ROOT)
                except (AttributeError, TypeError) as err:
                    raise ImproperlyConfigured(
                        f"Template {template.template.name} must either be "
                        f"configured with a 'dest' or STATIC_ROOT must be "
                        f"defined in settings, because it was not loaded from "
                        f"an app!"
                    ) from err

            dest /= template.template.name
        elif batch or Path(dest).is_dir():
            dest /= template.template.name

        os.makedirs(str(Path(dest if dest else '').parent), exist_ok=True)

        return Path(dest if dest else '')

    def render_to_disk(  # pylint: disable=R0913
            self,
            selector: str,
            context: Optional[Dict] = None,
            dest: Optional[Union[str, Path]] = None,
            first_engine: bool = False,
            first_loader: bool = False,
            first_preference: bool = False
    ) -> List[Render]:
        """
        Wrap render_each generator function and return the whole list of
        rendered templates for the given selector.

        :param selector: The name of the template to render to disk
        :param context: Additional context parameters that will override
            configured context parameters
        :param dest: Override the configured path to render the template at
            this path, either a string path, or Path like object. If the
            selector resolves to multiple templates, dest will be considered a
            directory. If the the selector resolves to a single template, dest
            will be considered the final file path, unless it already exists
            as a directory.
        :param first_engine: If true, render only the set of template names
            that match the selector that are found by the first rendering
            engine. By default (False) any templates that match the selector
            from any engine will be rendered.
        :param first_loader: If True, render only the set of template names
            from the first loader that matches any part of the selector. By
            default (False) any template name that matches the selector from
            any loader will be rendered.
        :param first_preference: If true, render only the templates that match
            the first preference for each loader. When combined with
            first_loader will render only the first preference(s) of the first
            loader. Preferences are loader specific and documented on the
            loader.
        :return: Render object for all the template(s) rendered to disk
        :raises TemplateDoesNotExist: if no template by the given name is found
        :raises ImproperlyConfigured: if not enough information was given to
            render and write the template
        """
        return [  # pylint: disable=R1721
            render for render in self.render_each(
                selector,
                context=context,
                dest=dest,
                first_engine=first_engine,
                first_loader=first_loader,
                first_preference=first_preference
            )
        ]

    def render_each(  # pylint: disable=R0914
            self,
            *selectors: str,
            context: Optional[Dict] = None,
            dest: Optional[Union[str, Path]] = None,
            first_engine: bool = False,
            first_loader: bool = False,
            first_preference: bool = False
    ) -> Generator[Render, None, None]:
        """
        A generator function that renders all selected templates of the highest
        precedence for each matching template name to disk.

        The location of the directory of the rendered template will either be
        based on the `dest` configuration parameter for the template or the app
         the template was found in.

        :param selectors: The name(s) of the template(s) to render to disk
        :param context: Additional context parameters that will override
            configured context parameters
        :param dest: Override the configured path to render the template at
            this path, either a string path, or Path like object. If the
            selector(s) resolve to multiple templates, dest will be considered
            a directory. If the the selector(s) resolve to a single template,
            dest will be considered the final file path, unless it already
            exists as a directory.
        :param first_engine: If true, render only the set of template names
            that match the selector that are found by the first rendering
            engine. By default (False) any templates that match the selector
            from any engine will be rendered.
        :param first_loader: If True, render only the set of template names
            from the first loader that matches any part of the selector. By
            default (False) any template name that matches the selector from
            any loader will be rendered.
        :param first_preference: If true, render only the templates that match
            the first preference for each loader. When combined with
            first_loader will render only the first preference(s) of the first
            loader. Preferences are loader specific and documented on the
            loader.
        :yield: Render objects for each template to disk
        :raises TemplateDoesNotExist: if no template by the given name is found
        :raises ImproperlyConfigured: if not enough information was given to
            render and write the template
        """
        if context:
            context = resolve_context(context)
        renders = []

        # all jobs are considered part of a batch if dest is provided and more
        # than one selector is provided
        batch = len(selectors) > 1 and dest
        for selector in selectors:
            config = self.templates.get(
                selector,
                StaticTemplateEngine.TemplateConfig(name=selector)
            )
            templates: Dict[str, Union[DjangoTemplate, Jinja2Template]] = {}
            chain = []
            for engine in self.all():
                try:
                    for template_name in engine.select_templates(
                            selector,
                            first_loader=first_loader,
                            first_preference=first_preference
                    ):
                        try:
                            templates.setdefault(
                                template_name,
                                engine.get_template(template_name)
                            )
                        except TemplateDoesNotExist as tdne:  # pragma: no cover
                            # this should be impossible w/o a loader bug!
                            if len(templates):
                                raise RuntimeError(
                                    f'Selector resolved to template '
                                    f'{template_name} which is not loadable: '
                                    f'{tdne}'
                                ) from tdne
                    if first_engine and templates:
                        break
                except TemplateDoesNotExist as tdne:
                    chain.append(tdne)
                    continue

            if not templates:
                raise TemplateDoesNotExist(selector, chain=chain)

            for name, template in templates.items():  # pylint: disable=W0612
                renders.append(
                    Render(
                        selector=selector,
                        config=config,
                        template=template,
                        destination=self.resolve_destination(
                            config,
                            template,
                            # each selector is a batch if it resolves to more
                            # than one template
                            bool(batch or len(templates) > 1),
                            dest
                        )
                    )
                )

        for render in renders:
            ctx = render.config.context.copy()
            if context is not None:
                ctx.update(context)
            with open(
                    str(render.destination), 'w', encoding='UTF-8'
            ) as temp_out:
                temp_out.write(
                    render.template.render({
                        **self.context,
                        **ctx
                    })
                )
            yield render
