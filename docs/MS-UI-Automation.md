**Microsoft UI Automation**

*Article • 09/15/2021*

> **Note**  
> This documentation is intended for .NET developers who want to use the managed UI Automation classes defined in the `System.Windows.Automation` namespace. For the latest information about UI Automation, see [Windows Automation API: UI Automation](https://docs.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32).

Microsoft UI Automation is an accessibility framework for Microsoft Windows. It addresses the needs of assistive technology products and automated test frameworks by providing programmatic access to information about the user interface (UI). In addition, UI Automation enables control and application developers to make their products accessible.

This documentation describes the UI Automation API for managed code. For information on programming for UI Automation in C++, see [UI Automation for Win32 Applications](https://docs.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32).

### In this section

- Accessibility Best Practices
- UI Automation Fundamentals
- UI Automation Providers for Managed Code
- UI Automation Clients for Managed Code
- UI Automation Control Patterns
- UI Automation Text Pattern
- UI Automation Control Types

### Related sections

- Accessibility Samples

---

## Accessibility best practices

*Article • 09/15/2021*

> **Note**  
> This article is intended for .NET Framework developers who want to use the managed UI Automation classes defined in the `System.Windows.Automation` namespace. For the latest information about UI Automation, see [Windows Automation API: UI Automation](https://docs.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32).

Implementing the following best practices in controls or applications will improve their accessibility for people who use assistive technology devices. Many of these best practices focus on good user interface (UI) design. Each best practice includes implementation information for Windows Presentation Foundation (WPF) controls or applications. In many cases, the work to meet these best practices is already included in WPF controls.

### Programmatic Access

Programmatic access involves ensuring that all UI elements are labeled, property values are exposed, and appropriate events are raised. For standard WPF controls, most of this work is already done through `AutomationPeer`. Custom controls require additional work to ensure that programmatic access is correctly implemented.

#### Enable Programmatic Access to all UI Elements and Text

User interface (UI) elements should enable programmatic access. If the UI is a standard WPF control, support for programmatic access is included in the control. If the control is a custom control – a control that has been subclassed from a common control or a control that has been subclassed from `Control` – then you must check the `AutomationPeer` implementation for areas that may need modification.

Following this best practice allows assistive technology vendors to identify and manipulate elements of your product's UI.

#### Place Names, Titles, and Descriptions on UI Objects, Frames, and Pages

Assistive technologies, especially screen readers, use the title to understand the location of the frame, object, or page in the navigation scheme. Therefore, the title must be descriptive. For example, a Web page title of "Microsoft Web Page" is useless if the user has navigated deeply into some particular area. A descriptive title is critical for users who are blind and depend on screen readers. Similarly, for WPF controls, `NameProperty` and `HelpTextProperty` are important for assistive technology devices.

Following this best practice allows assistive technologies to identify and manipulate UI in sample controls and applications.

#### Ensure Programmatic Events Are Triggered by All UI Activities

Following this best practice allows assistive technologies to listen for changes in the UI and notify the user about these changes.

### User Settings

The best practice in this section ensures that controls or applications do not override user settings.

#### Respect All System-Wide Settings and Do Not Interfere with Accessibility Functions

Users can use the Control Panel to set some system-wide flags; other flags can be set programmatically. These settings should not be changed by controls or applications. Also, applications must support the accessibility settings of their host operating system.

Following this best practice allows users to set accessibility settings and know that those settings will not be changed by applications.

### Visual UI Design

Best Practices in this section ensure that controls or applications use color and images effectively and are able to be used by Assistive technologies.

#### Don't Hard-Code Colors

People who are color blind, have low vision, or are using a black and white screen might not be able to use applications with hard-coded colors.

Following this best practice allows users to adjust color combinations based on individual needs.

#### Support High Contrast and all System Display Attributes

Applications should not disrupt or disable user-selected, system-wide contrast settings, color selections, or other system-wide display settings and attributes. System-wide settings adopted by a user enhance the accessibility of applications, so they should not be disabled or disregarded by applications. Color should be used in their correct foreground-on-background combination to provide proper contrast. Don't mix unrelated colors, and don't reverse colors.

Many users require specific high-contrast combinations, such as white text on a black background. Drawing these reversed, as black text on a white background causes the background to bleed over the foreground and can make reading difficult for some users.

#### Ensure All UI Correctly Scales by Any DPI Setting

Ensure that all UI can correctly scale by any dots per inch (dpi) setting. Also, ensure that UI elements fit in a screen of 1024 × 768 with 120 dots per inch (dpi).

### Navigation

Best Practices in this section ensure that navigation has been addressed for controls and applications.

#### Provide Keyboard Interface for All UI Elements

Tab stops, especially when carefully planned, give users another way to navigate the UI.

Applications should provide the following keyboard interfaces:

- tab stops for all controls that the user can interact with, such as buttons, links, or list boxes
- logical tab order

#### Show the Keyboard Focus

Users need to know which object has the keyboard focus so that they can anticipate the effect of their keystrokes. To highlight the keyboard focus, use colors, fonts, or graphics such as rectangles or magnification. To audibly highlight the keyboard focus, change the volume, pitch, or tonal quality.

To avoid confusion, applications should hide all visual focus indicators and dim selections that are located in inactive windows (or panes).

Applications should do the following with keyboard focus:

- one item should always have keyboard focus
- keyboard focus should be visible and obvious
- selections and/or focused items should be visually highlighted

#### Support Navigation Standards and Powerful Navigation Schemes

Different aspects of keyboard navigation provide different ways for users to navigate the UI.

Applications should provide the following keyboard interfaces:

- shortcut keys and underlined access keys for all commands, menus, and controls
- keyboard shortcuts to important links
- all menu items have an access key; all buttons have accelerator keys, all commands have an accelerator key.

#### Do Not Let Mouse Location Interfere with Keyboard Navigation

Mouse location should not interfere with keyboard navigation. For example, if the mouse is positioned some place and the user is navigating with the keyboard, a mouse click should not happen unless initiated by the user.

### Multimodal Interface

Best Practices in this section ensure that application UI includes alternatives for visual elements.

#### Provide User-Selectable Equivalents for Non-Text Elements

For each non-text element, provide a user-selectable equivalent for text, transcripts, or audio descriptions, such as alt text, captions, or visual feedback.

Non-text elements cover a wide range of UI elements including: images, image map regions, animations, applets, frames, scripts, graphical buttons, sounds, stand-alone audio files, and video. Non-text elements are important when they contain visual information, speech, or general audio information that the user needs access to in order to understand the content of the UI.

#### Use Color but Also Provide Alternatives to Color

Use color to enhance, emphasize, or reiterate information shown by other means, but do not communicate information by using color alone. Users who are color blind or have a monochrome display need alternatives to color.

#### Use Standard Input APIs with Device-Independent Calls

Device-independent calls ensure keyboard and mouse feature equality, while providing assistive technology with needed information about the UI.

### See also

- `System.Windows.Automation.Peers`
- NumericUpDown Custom Control with Theme and UI Automation Support Sample
- Guidelines for Keyboard User Interface Design

---

## UI Automation Fundamentals

*Article • 09/15/2021*

> **Note**  
> This documentation is intended for .NET Framework developers who want to use the managed UI Automation classes defined in the `System.Windows.Automation` namespace. For the latest information about UI Automation, see [Windows Automation API: UI Automation](https://docs.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32).

This section contains high-level overviews of the Microsoft UI Automation API.

### In This Section

- UI Automation Overview
- UI Automation and Microsoft Active Accessibility
- UI Automation Tree Overview
- UI Automation Control Patterns Overview
- UI Automation Properties Overview
- UI Automation Events Overview
- UI Automation Security Overview
- Using UI Automation for Automated Testing

### Reference

- `System.Windows.Automation`
- `System.Windows.Automation.Provider`
- `System.Windows.Automation.Text`
- `UIAutomationClientsideProviders`

---

## UI Automation Overview

*Article • 09/15/2021*

> **Note**  
> This documentation is intended for .NET Framework developers who want to use the managed UI Automation classes defined in the `System.Windows.Automation` namespace. For the latest information about UI Automation, see [Windows Automation API: UI Automation](https://docs.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32).

Microsoft UI Automation is the new accessibility framework for Microsoft Windows, available on all operating systems that support Windows Presentation Foundation (WPF).

UI Automation provides programmatic access to most user interface (UI) elements on the desktop, enabling assistive technology products such as screen readers to provide information about the UI to end users and to manipulate the UI by means other than standard input. UI Automation also allows automated test scripts to interact with the UI.

> **Note**  
> UI Automation does not enable communication between processes started by different users through the **Run as** command.

UI Automation client applications can be written with the assurance that they will work on multiple frameworks. The UI Automation core masks any differences in the frameworks that underlie various pieces of UI. For example, the `Content` property of a WPF button, the `Caption` property of a Win32 button, and the `ALT` property of an HTML image are all mapped to a single property, `Name`, in the UI Automation view.

UI Automation provides full functionality on supported Windows operating systems running the .NET Framework (see .NET Framework system requirements) or versions of .NET Core starting with .NET Core 3.0.

UI Automation providers offer some support for Microsoft Active Accessibility client applications through a built-in bridging service.

### Providers and Clients

UI Automation has four main components, as shown in the following table.

| Component | Description |
|-----------|-------------|
| Provider API (`UIAutomationProvider.dll` and `UIAutomationTypes.dll`) | A set of interface definitions that are implemented by UI Automation providers, objects that provide information about UI elements and respond to programmatic input. |
| Client API (`UIAutomationClient.dll` and `UIAutomationTypes.dll`) | A set of types for managed code that enables UI Automation client applications to obtain information about the UI and to send input to controls. |
| `UiAutomationCore.dll` | The underlying code (sometimes called the UI Automation core) that handles communication between providers and clients. |
| `UIAutomationClientsideProviders.dll` | A set of UI Automation providers for standard legacy controls. (WPF controls have native support for UI Automation.) This support is automatically available to client applications. |

From the software developer's perspective, there are two ways of using UI Automation: to create support for custom controls (using the provider API), and creating applications that use the UI Automation core to communicate with UI elements (using the client API). Depending on your focus, you should refer to different parts of the documentation. You can learn more about the concepts and gain practical how-to knowledge in the following sections.

| Section | Subject matter | Audience |
|---------|----------------|----------|
| UI Automation Fundamentals (this section) | Broad overviews of the concepts. | All. |
| UI Automation Providers for Managed Code | Overviews and how-to topics to help you use the provider API. | Control developers. |
| UI Automation Clients for Managed Code | Overviews and how-to topics to help you use the client API. | Client application developers. |
| UI Automation Control Patterns | Information about how control patterns should be implemented by providers, and what functionality is available to clients. | All. |
| UI Automation Text Pattern | Information about how the Text control pattern should be implemented by providers, and what functionality is available to clients. | All. |
| UI Automation Control Types | Information about the properties and control patterns supported by different control types. | All. |

The following table lists UI Automation namespaces, the DLLs that contain them, and the audience that uses them.

| Namespace | Referenced DLLs | Audience |
|-----------|-----------------|----------|
| `System.Windows.Automation` | `UIAutomationClient`, `UIAutomationTypes` | UI Automation client developers; used to find `AutomationElement` objects, register for UI Automation events, and work with UI Automation control patterns. |
| `System.Windows.Automation.Provider` | `UIAutomationProvider`, `UIAutomationTypes` | Developers of UI Automation providers for frameworks other than WPF. |
| `System.Windows.Automation.Text` | `UIAutomationClient`, `UIAutomationTypes` | Developers of UI Automation providers for frameworks other than WPF; used to implement the `TextPattern` control pattern. |
| `System.Windows.Automation.Peers` | `PresentationFramework` | Developers of UI Automation providers for WPF. |

### UI Automation Model

UI Automation exposes every piece of the UI to client applications as an `AutomationElement`. Elements are contained in a tree structure, with the desktop as the root element. Clients can filter the raw view of the tree as a control view or a content view. Applications can also create custom views.

`AutomationElement` objects expose common properties of the UI elements they represent. One of these properties is the control type, which defines its basic appearance and functionality as a single recognizable entity: for example, a button or check box.

In addition, elements expose control patterns that provide properties specific to their control types. Control patterns also expose methods that enable clients to get further information about the element and to provide input.

> **Note**  
> There is not a one-to-one correspondence between control types and control patterns. A control pattern may be supported by multiple control types, and a control may support multiple control patterns, each of which exposes different aspects of its behavior. For example, a combo box has at least two control patterns: one that represents its ability to expand and collapse, and another that represents the selection mechanism. For specifics, see UI Automation Control Types.

UI Automation also provides information to client applications through events. Unlike WinEvents, UI Automation events are not based on a broadcast mechanism. UI Automation clients register for specific event notifications and can request that specific UI Automation properties and control pattern information be passed into their event handlers. In addition, a UI Automation event contains a reference to the element that raised it. Providers can improve performance by raising events selectively, depending on whether any clients are listening.

### See also

- UI Automation Tree Overview
- UI Automation Control Patterns Overview
- UI Automation Properties Overview
- UI Automation Events Overview
- UI Automation Security Overview

---

## UI Automation and Microsoft Active Accessibility

*Article • 09/15/2021*

> **Note**  
> This documentation is intended for .NET Framework developers who want to use the managed UI Automation classes defined in the `System.Windows.Automation` namespace. For the latest information about UI Automation, see [Windows Automation API: UI Automation](https://docs.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32).

Microsoft Active Accessibility was the earlier solution for making applications accessible. Microsoft UI Automation is the new accessibility model for Microsoft Windows and is intended to address the needs of assistive technology products and automated testing tools. UI Automation offers many improvements over Active Accessibility.

This topic includes the main features of UI Automation and explains how these features differ from Active Accessibility.

### Programming Languages

Active Accessibility is based on the Component Object Model (COM) with support for dual interfaces, and is therefore programmable in C/C++, Microsoft Visual Basic 6.0, and scripting languages. UI Automation (including the client-side provider library for standard controls) is written in managed code, and UI Automation client applications are most easily programmed using C# or Visual Basic .NET. UI Automation providers, which are interface implementations, can be written in managed code or in C/C++.

### Support in Windows Presentation Foundation

Windows Presentation Foundation (WPF) is the new model for creating user interfaces. WPF elements do not contain native support for Active Accessibility; however, they do support UI Automation, which includes bridging support for Active Accessibility clients. Only clients written specifically for UI Automation can take full advantage of the accessibility features of WPF, such as the rich support for text.

### Servers and Clients

In Active Accessibility, servers and clients communicate directly, largely through the server's implementation of `IAccessible`.

In UI Automation, a core service lies between the server (called a provider) and the client. The core service makes calls to the interfaces implemented by providers and provides additional services such as generating unique runtime identifiers for elements. Client applications use library functions to call the UI Automation service.

UI Automation providers can provide information to Active Accessibility clients, and Active Accessibility servers can provide information to UI Automation client applications. However, because Active Accessibility does not expose as much information as UI Automation, the two models are not fully compatible.

### UI Elements

Active Accessibility presents UI elements either as an `IAccessible` interface or as a child identifier. It is difficult to compare two `IAccessible` pointers to determine if they refer to the same element.

In UI Automation, every element is represented as an `AutomationElement` object. Comparison is done by using the equality operator or the `Equals` method, both of which compare the unique runtime identifiers of the elements.

### Tree Views and Navigation

The user interface (UI) elements on the screen can be seen as a tree structure with the desktop as the root, application windows as immediate children, and elements within applications as further descendants.

In Active Accessibility, many automation elements that are irrelevant to end users are exposed in the tree. Client applications have to look at all the elements to determine which are meaningful.

UI Automation client applications see the UI through a filtered view. The view contains only elements of interest: those that give information to the user or enable interaction. Predefined views of only control elements and only content elements are available; in addition, applications can define custom views. UI Automation simplifies the task of describing the UI to the user and helping the user interact with the application.

Navigation between elements, in Active Accessibility, is either spatial (for example, moving to the element that lies to the left on the screen), logical (for example, moving to the next menu item, or the next item in the tab order within a dialog box), or hierarchical (for example, moving the first child in a container, or from the child to its parent). Hierarchical navigation is complicated by the fact that child elements are not always objects that implement `IAccessible`.

In UI Automation, all UI elements are `AutomationElement` objects that support the same basic functionality. (From the standpoint of the provider, they are objects that implement an interface inherited from `IRawElementProviderSimple`.) Navigation is mainly hierarchical: from parents to children, and from one sibling to the next. (Navigation between siblings has a logical element, as it may follow the tab order.) You can navigate from any starting-point, using any filtered view of the tree, by using the `TreeWalker` class. You can also navigate to particular children or descendants by using `FindFirst` and `FindAll`; for example, it is very easy to retrieve all elements within a dialog box that support a specified control pattern.

Navigation in UI Automation is more consistent than in Active Accessibility. Some elements such as drop-down lists and pop-up windows appear twice in the Active Accessibility tree, and navigation from them may have unexpected results. It is actually impossible to properly implement Active Accessibility for a rebar control. UI Automation enables reparenting and repositioning, so that an element can be placed anywhere in the tree despite the hierarchy imposed by ownership of windows.

### Roles and Control Types

Active Accessibility uses the `accRole` property (`IAccessible::get_accRole`) to retrieve a description of the element's role in the UI, such as `ROLE_SYSTEM_SLIDER` or `ROLE_SYSTEM_MENUITEM`. The role of an element is the main clue to its available functionality. Interaction with a control is achieved by using fixed methods such as `IAccessible::accSelect` and `IAccessible::accDoDefaultAction`. The interaction between the client application and the UI is limited to what can be done through `IAccessible`.

In contrast, UI Automation largely decouples the control type of the element (described by the `ControlType` property) from its expected functionality. Functionality is determined by the control patterns that are supported by the provider through its implementation of specialized interfaces. Control patterns can be combined to describe the full set of functionality supported by a particular UI element. Some providers are required to support a particular control pattern; for example, the provider for a check box must support the Toggle control pattern. Other providers are required to support one or more of a set of control patterns; for example, a button must support either Toggle or Invoke. Still others support no control patterns at all; for example, a pane that cannot be moved, resized, or docked does not have any control patterns.

UI Automation supports custom controls, which are identified by the `Custom` property and can be described by the `LocalizedControlTypeProperty` property.

The following table shows the mapping of Active Accessibility roles to UI Automation control types.

| Active Accessibility role | UI Automation control type |
|---------------------------|----------------------------|
| `ROLE_SYSTEM_PUSHBUTTON` | Button |
| `ROLE_SYSTEM_CLIENT` | Calendar |
| `ROLE_SYSTEM_CHECKBUTTON` | Check box |
| `ROLE_SYSTEM_COMBOBOX` | Combo box |
| `ROLE_SYSTEM_CLIENT` | Custom |
| `ROLE_SYSTEM_LIST` | Data grid |
| `ROLE_SYSTEM_LISTITEM` | Data item |
| `ROLE_SYSTEM_DOCUMENT` | Document |
| `ROLE_SYSTEM_TEXT` | Edit |
| `ROLE_SYSTEM_GROUPING` | Group |
| `ROLE_SYSTEM_LIST` | Header |
| `ROLE_SYSTEM_COLUMNHEADER` | Header item |
| `ROLE_SYSTEM_LINK` | Hyperlink |
| `ROLE_SYSTEM_GRAPHIC` | Image |
| `ROLE_SYSTEM_LIST` | List |
| `ROLE_SYSTEM_LISTITEM` | List item |
| `ROLE_SYSTEM_MENUPOPUP` | Menu |
| `ROLE_SYSTEM_MENUBAR` | Menu bar |
| `ROLE_SYSTEM_MENUITEM` | Menu item |
| `ROLE_SYSTEM_PANE` | Pane |
| `ROLE_SYSTEM_PROGRESSBAR` | Progress bar |
| `ROLE_SYSTEM_RADIOBUTTON` | Radio button |
| `ROLE_SYSTEM_SCROLLBAR` | Scroll bar |
| `ROLE_SYSTEM_SEPARATOR` | Separator |
| `ROLE_SYSTEM_SLIDER` | Slider |
| `ROLE_SYSTEM_SPINBUTTON` | Spinner |
| `ROLE_SYSTEM_SPLITBUTTON` | Split button |
| `ROLE_SYSTEM_STATUSBAR` | Status bar |
| `ROLE_SYSTEM_PAGETABLIST` | Tab |
| `ROLE_SYSTEM_PAGETAB` | Tab item |
| `ROLE_SYSTEM_TABLE` | Table |
| `ROLE_SYSTEM_STATICTEXT` | Text |
| `ROLE_SYSTEM_INDICATOR` | Thumb |
| `ROLE_SYSTEM_TITLEBAR` | Title bar |
| `ROLE_SYSTEM_TOOLBAR` | Tool bar |
| `ROLE_SYSTEM_TOOLTIP` | ToolTip |
| `ROLE_SYSTEM_OUTLINE` | Tree |
| `ROLE_SYSTEM_OUTLINEITEM` | Tree item |
| `ROLE_SYSTEM_WINDOW` | Window |

For more information about the different control types, see UI Automation Control Types.

### States and Properties

In Active Accessibility, elements support a common set of properties, and some properties (such as `accState`) must describe very different things, depending on the element's role. Servers must implement all methods of `IAccessible` that return a property, even those that are not relevant to the element.

UI Automation defines many more properties, some of which correspond to states in Active Accessibility. Some are common to all elements, but others are specific to control types and control patterns. Properties are distinguished by unique identifiers, and most properties can be retrieved by using a single method, `GetCurrentPropertyValue` or `GetCachedPropertyValue`. Many properties are also easily retrievable from the `Current` and `Cached` property accessors.

A UI Automation provider does not have to implement irrelevant properties, but can simply return a `null` value for any properties it does not support. Also, the UI Automation core service can obtain some properties from the default window provider, and these are amalgamated with properties explicitly implemented by the provider.

As well as supporting many more properties, UI Automation supplies better performance by allowing multiple properties to be retrieved with a single cross-process call.

The following table shows the correspondence between properties in the two models.

| Active Accessibility property accessor | UI Automation property ID | Remarks |
|----------------------------------------|---------------------------|---------|
| `get_accKeyboardShortcut` | `AccessKeyProperty` or `AcceleratorKeyProperty` | `AccessKeyProperty` takes precedence if both are present. |
| `get_accName` | `NameProperty` | |
| `get_accRole` | `ControlTypeProperty` | See the previous table for mapping of roles to control types. |
| `get_accValue` | `ValuePattern.ValueProperty`<br>`RangeValuePattern.ValueProperty` | Valid only for control types that support `ValuePattern` or `RangeValuePattern`. RangeValue values are normalized to 0-100, to be consistent with MSAA behavior. Value items use a string. |
| `get_accHelp` | `HelpTextProperty` | |
| `accLocation` | `BoundingRectangleProperty` | |
| `get_accDescription` | Not supported in UI Automation | `accDescription` did not have a clear specification within MSAA, which resulted in providers placing different pieces of information in this property. |
| `get_accHelpTopic` | Not supported in UI Automation | |

The following table shows which UI Automation properties correspond to Active Accessibility state constants.

| Active Accessibility state | UI Automation property | Triggers State Change? |
|----------------------------|------------------------|------------------------|
| `STATE_SYSTEM_CHECKED` | For check box, `ToggleStateProperty`<br>For radio button, `IsSelectedProperty` | Y |
| `STATE_SYSTEM_COLLAPSED` | `ExpandCollapseState = Collapsed` | Y |
| `STATE_SYSTEM_EXPANDED` | `ExpandCollapseState = Expanded` or `PartiallyExpanded` | Y |
| `STATE_SYSTEM_FOCUSABLE` | `IsKeyboardFocusableProperty` | N |
| `STATE_SYSTEM_FOCUSED` | `HasKeyboardFocusProperty` | N |
| `STATE_SYSTEM_HASPOPUP` | `ExpandCollapsePattern` for menu items | N |
| `STATE_SYSTEM_INVISIBLE` | `IsOffscreenProperty = True` and `GetClickablePoint` causes `NoClickablePointException` | N |
| `STATE_SYSTEM_LINKED` | `ControlTypeProperty = Hyperlink` | N |
| `STATE_SYSTEM_MIXED` | `ToggleState = Indeterminate` | N |
| `STATE_SYSTEM_MOVEABLE` | `CanMoveProperty` | N |
| `STATE_SYSTEM_MULTISELECTABLE` | `CanSelectMultipleProperty` | N |
| `STATE_SYSTEM_OFFSCREEN` | `IsOffscreenProperty = True` | N |
| `STATE_SYSTEM_PROTECTED` | `IsPasswordProperty` | N |
| `STATE_SYSTEM_READONLY` | `RangeValuePattern.IsReadOnlyProperty` and `ValuePattern.IsReadOnlyProperty` | N |
| `STATE_SYSTEM_SELECTABLE` | `SelectionItemPattern` is supported | N |
| `STATE_SYSTEM_SELECTED` | `IsSelectedProperty` | N |
| `STATE_SYSTEM_SIZEABLE` | `CanResize` | N |
| `STATE_SYSTEM_UNAVAILABLE` | `IsEnabledProperty` | Y |

The following states either were not implemented by most Active Accessibility control servers or have no equivalent in UI Automation.

| Active Accessibility state | Remarks |
|----------------------------|---------|
| `STATE_SYSTEM_BUSY` | Not available in UI Automation |
| `STATE_SYSTEM_DEFAULT` | Not available in UI Automation |
| `STATE_SYSTEM_ANIMATED` | Not available in UI Automation |
| `STATE_SYSTEM_EXTSELECTABLE` | Not widely implemented by Active Accessibility servers |
| `STATE_SYSTEM_MARQUEED` | Not widely implemented by Active Accessibility servers |
| `STATE_SYSTEM_SELFVOICING` | Not widely implemented by Active Accessibility servers |
| `STATE_SYSTEM_TRAVERSED` | Not available in UI Automation |
| `STATE_SYSTEM_ALERT_HIGH` | Not widely implemented by Active Accessibility servers |
| `STATE_SYSTEM_ALERT_MEDIUM` | Not widely implemented by Active Accessibility servers |
| `STATE_SYSTEM_ALERT_LOW` | Not widely implemented by Active Accessibility servers |
| `STATE_SYSTEM_FLOATING` | Not widely implemented by Active Accessibility servers |
| `STATE_SYSTEM_HOTTRACKED` | Not available in UI Automation |
| `STATE_SYSTEM_PRESSED` | Not available in UI Automation |

For a complete list of UI Automation property identifiers, see UI Automation Properties Overview.

### Events

The event mechanism in UI Automation, unlike that in Active Accessibility, does not rely on Windows event routing (which is closely tied in with window handles) and does not require the client application to set up hooks. Subscriptions to events can be fine-tuned not just to particular events but to particular parts of the tree. Providers can also fine-tune their raising of events by keeping track of what events are being listened for.

It is also easier for clients to retrieve the elements that raise events, as these are passed directly to the event callback. Properties of the element are automatically prefetched if a cache request was active when the client subscribed to the event.

The following table shows the correspondence of Active Accessibility WinEvents and UI Automation events.

| WinEvent | UI Automation event identifier |
|----------|--------------------------------|
| `EVENT_OBJECT_ACCELERATORCHANGE` | `AcceleratorKeyProperty` property change |
| `EVENT_OBJECT_CONTENTSCROLLED` | `VerticalScrollPercentProperty` or `HorizontalScrollPercentProperty` property change on the associated scroll bars |
| `EVENT_OBJECT_CREATE` | `StructureChangedEvent` |
| `EVENT_OBJECT_DEFACTIONCHANGE` | No equivalent |
| `EVENT_OBJECT_DESCRIPTIONCHANGE` | No exact equivalent; perhaps `HelpTextProperty` or `LocalizedControlTypeProperty` property change |
| `EVENT_OBJECT_DESTROY` | `StructureChangedEvent` |
| `EVENT_OBJECT_FOCUS` | `AutomationFocusChangedEvent` |
| `EVENT_OBJECT_HELPCHANGE` | `HelpTextProperty` change |
| `EVENT_OBJECT_HIDE` | `StructureChangedEvent` |
| `EVENT_OBJECT_LOCATIONCHANGE` | `BoundingRectangleProperty` property change |
| `EVENT_OBJECT_NAMECHANGE` | `NameProperty` property change |
| `EVENT_OBJECT_PARENTCHANGE` | `StructureChangedEvent` |
| `EVENT_OBJECT_REORDER` | Not consistently used in Active Accessibility. No directly corresponding event is defined in UI Automation. |
| `EVENT_OBJECT_SELECTION` | `ElementSelectedEvent` |
| `EVENT_OBJECT_SELECTIONADD` | `ElementAddedToSelectionEvent` |
| `EVENT_OBJECT_SELECTIONREMOVE` | `ElementRemovedFromSelectionEvent` |
| `EVENT_OBJECT_SELECTIONWITHIN` | No equivalent |
| `EVENT_OBJECT_SHOW` | `StructureChangedEvent` |
| `EVENT_OBJECT_STATECHANGE` | Various property-changed events |
| `EVENT_OBJECT_VALUECHANGE` | `RangeValuePattern.ValueProperty` and `ValuePattern.ValueProperty` changed |
| `EVENT_SYSTEM_ALERT` | No equivalent |
| `EVENT_SYSTEM_CAPTUREEND` | No equivalent |
| `EVENT_SYSTEM_CAPTURESTART` | No equivalent |
| `EVENT_SYSTEM_CONTEXTHELPEND` | No equivalent |
| `EVENT_SYSTEM_CONTEXTHELPSTART` | No equivalent |
| `EVENT_SYSTEM_DIALOGEND` | `WindowClosedEvent` |
| `EVENT_SYSTEM_DIALOGSTART` | `WindowOpenedEvent` |
| `EVENT_SYSTEM_DRAGDROPEND` | No equivalent |
| `EVENT_SYSTEM_DRAGDROPSTART` | No equivalent |
| `EVENT_SYSTEM_FOREGROUND` | `AutomationFocusChangedEvent` |
| `EVENT_SYSTEM_MENUEND` | `MenuClosedEvent` |
| `EVENT_SYSTEM_MENUPOPUPEND` | `MenuClosedEvent` |
| `EVENT_SYSTEM_MENUPOPUPSTART` | `MenuOpenedEvent` |
| `EVENT_SYSTEM_MENUSTART` | `MenuOpenedEvent` |
| `EVENT_SYSTEM_MINIMIZEEND` | `WindowVisualStateProperty` property change |
| `EVENT_SYSTEM_MINIMIZESTART` | `WindowVisualStateProperty` property change |
| `EVENT_SYSTEM_MOVESIZEEND` | `BoundingRectangleProperty` property change |
| `EVENT_SYSTEM_MOVESIZESTART` | `BoundingRectangleProperty` property change |
| `EVENT_SYSTEM_SCROLLINGEND` | `VerticalScrollPercentProperty` or `HorizontalScrollPercentProperty` property change |
| `EVENT_SYSTEM_SCROLLINGSTART` | `VerticalScrollPercentProperty` or `HorizontalScrollPercentProperty` property change |
| `EVENT_SYSTEM_SOUND` | No equivalent |
| `EVENT_SYSTEM_SWITCHEND` | No equivalent, but an `AutomationFocusChangedEvent` event signals that a new application has received the focus |
| `EVENT_SYSTEM_SWITCHSTART` | No equivalent |
| No equivalent | `CurrentViewProperty` property change |
| No equivalent | `HorizontallyScrollableProperty` property change |
| No equivalent | `VerticallyScrollableProperty` property change |
| No equivalent | `HorizontalScrollPercentProperty` property change |
| No equivalent | `VerticalScrollPercentProperty` property change |
| No equivalent | `HorizontalViewSizeProperty` property change |
| No equivalent | `VerticalViewSizeProperty` property change |
| No equivalent | `ToggleStateProperty` property change |
| No equivalent | `WindowVisualStateProperty` property change |
| No equivalent | `AsyncContentLoadedEvent` event |
| No equivalent | `ToolTipOpenedEvent` |

### Security

Some `IAccessible` customization scenarios require wrapping a base `IAccessible` and calling through to it. This has security implications, since a partially trusted component should not be an intermediary on a code path.

The UI Automation model removes the need for providers to call through to other provider code. The UI Automation core service does all the necessary aggregation.

### See also

- UI Automation Fundamentals

---

## UI Automation Tree Overview

*Article • 09/15/2021*

> **Note**  
> This documentation is intended for .NET Framework developers who want to use the managed UI Automation classes defined in the `System.Windows.Automation` namespace. For the latest information about UI Automation, see [Windows Automation API: UI Automation](https://docs.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32).

Assistive technology products and test scripts navigate the UI Automation tree to gather information about the user interface (UI) and its elements.

Within the UI Automation tree there is a root element (`RootElement`) that represents the current desktop and whose child elements represent application windows. Each of these child elements can contain elements representing pieces of UI such as menus, buttons, toolbars, and list boxes. These elements in turn can contain elements such as list items.

The UI Automation tree is not a fixed structure and is seldom seen in its totality because it might contain thousands of elements. Parts of it are built as they are needed, and it can undergo changes as elements are added, moved, or removed.

UI Automation providers support the UI Automation tree by implementing navigation among items within a fragment, which consists of a root (usually hosted in a window) and a subtree. However, providers are not concerned with navigation from one control to another. This is managed by the UI Automation core, using information from the default window providers.

### Views of the Automation Tree

The UI Automation tree can be filtered to create views that contain only those `AutomationElement` objects relevant for a particular client. This approach allows clients to customize the structure presented through UI Automation to their particular needs.

The client has two ways of customizing the view: by scoping and by filtering. Scoping is defining the extent of the view, starting from a base element: for example, the application might want to find only direct children of the desktop, or all descendants of an application window. Filtering is defining the types of elements that are to be included in the view.

UI Automation providers support filtering by defining properties on elements, including the `IsControlElementProperty` and `IsContentElementProperty` properties.

UI Automation provides three default views. These views are defined by the type of filtering performed; the scope of any view is defined by the application. In addition, the application can apply other filters on properties; for example, to include only enabled controls in a control view.

#### Raw View

The raw view of the UI Automation tree is the full tree of `AutomationElement` objects for which the desktop is the root. The raw view closely follows the native programmatic structure of an application and therefore is the most detailed view available. It is also the base on which the other views of the tree are built. Because this view depends on the underlying UI framework, the raw view of a WPF button will have a different raw view than a Win32 button.

The raw view is obtained by searching for elements without specifying properties or by using the `RawViewWalker` to navigate the tree.

#### Control View

The control view of the UI Automation tree simplifies the assistive technology product's task of describing the UI to the end user and helping that end user interact with the application because it closely maps to the UI structure perceived by an end user.

The control view is a subset of the raw view. It includes all UI items from the raw view that an end user would understand as interactive or contributing to the logical structure of the control in the UI. Examples of UI items that contribute to the logical structure of the UI, but are not interactive themselves, are item containers such as list view headers, toolbars, menus, and the status bar. Non-interactive items used simply for layout or decorative purposes will not be seen in the control view. An example is a panel that was used only to lay out the controls in a dialog but does not itself contain any information. Non-interactive items that will be seen in the control view are graphics with information and static text in a dialog. Non-interactive items that are included in the control view cannot receive keyboard focus.

The control view is obtained by searching for elements that have the `IsControlElement` property set to `true`, or by using the `ControlViewWalker` to navigate the tree.

#### Content View

The content view of the UI Automation tree is a subset of the control view. It contains UI items that convey the true information in a user interface, including UI items that can receive keyboard focus and some text that is not a label on a UI item. For example, the values in a drop-down combo box will appear in the content view because they represent the information being used by an end user. In the content view, a combo box and list box are both represented as a collection of UI items where one, or perhaps more than one, item can be selected. The fact that one is always open and one can expand and collapse is irrelevant in the content view because it is designed to show the data, or content, that is being presented to the user.

The content view is obtained by searching for elements that have the `IsContentElement` property set to `true`, or by using the `ContentViewWalker` to navigate the tree.

### See also

- `AutomationElement`
- UI Automation Overview

---

## UI Automation Control Patterns Overview

*Article • 09/15/2021*

> **Note**  
> This documentation is intended for .NET Framework developers who want to use the managed UI Automation classes defined in the `System.Windows.Automation` namespace. For the latest information about UI Automation, see [Windows Automation API: UI Automation](https://docs.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32).

This overview introduces Microsoft UI Automation control patterns. Control patterns provide a way to categorize and expose a control's functionality independent of the control type or the appearance of the control.

UI Automation uses control patterns to represent common control behaviors. For example, you use the Invoke control pattern for controls that can be invoked (such as buttons) and the Scroll control pattern for controls that have scroll bars (such as list boxes, list views, or combo boxes). Because each control pattern represents a separate functionality, they can be combined to describe the full set of functionality supported by a particular control.

> **Note**  
> Aggregate controls—built with child controls that provide the user interface (UI) for functionality exposed by the parent—should implement all control patterns normally associated with each child control. In turn, those same control patterns are not required to be implemented by the child controls.

### UI Automation Control Pattern Components

Control patterns support the methods, properties, events, and relationships needed to define a discrete piece of functionality available in a control.

- The relationship between a UI Automation element and its parent, children and siblings describes the element's structure within the UI Automation tree.
- The methods allow UI Automation clients to manipulate the control.
- The properties and events provide information about the control pattern's functionality as well as information about the state of the control.

Control patterns relate to UI as interfaces relate to Component Object Model (COM) objects. In COM, you can query an object to ask what interfaces it supports and then use those interfaces to access functionality. In UI Automation, UI Automation clients can ask a control which control patterns it supports and then interact with the control through the properties, methods, events, and structures exposed by the supported control patterns. For example, for a multiline edit box, UI Automation providers implement `IScrollProvider`. When a client knows that an `AutomationElement` supports the `ScrollPattern` control pattern, it can use the properties, methods, and events exposed by that control pattern to manipulate the control, or access information about the control.

### UI Automation Providers and Clients

UI Automation providers implement control patterns to expose the appropriate behavior for a specific piece of functionality supported by the control.

UI Automation clients access methods and properties of UI Automation control pattern classes and use them to get information about the UI, or to manipulate the UI. These control pattern classes are found in the `System.Windows.Automation` namespace (for example, `InvokePattern` and `SelectionPattern`).

Clients use `AutomationElement` methods (such as `AutomationElement.GetCurrentPropertyValue` or `AutomationElement.GetCachedPropertyValue`) or the common language runtime (CLR) accessors to access the UI Automation properties on a pattern. Each control pattern class has a field member (for example, `InvokePattern.Pattern` or `SelectionPattern.Pattern`) that identifies that control pattern and can be passed as a parameter to `GetCachedPattern` or `GetCurrentPattern` to retrieve that pattern for an `AutomationElement`.

### Dynamic Control Patterns

Some controls do not always support the same set of control patterns. Control patterns are considered supported when they are available to a UI Automation client. For example, a multiline edit box enables vertical scrolling only when it contains more lines of text than can be displayed in its viewable area. Scrolling is disabled when enough text is removed so that scrolling is no longer required. For this example, the `ScrollPattern` control pattern is dynamically supported depending on the current state of the control (how much text is in the edit box).

### Control Pattern Classes and Interfaces

The following table describes the UI Automation control patterns. The table also lists the classes used by UI Automation clients to access the control patterns, as well as the interfaces used by UI Automation providers to implement them.

| Control Pattern Class | Provider Interface | Description |
|-----------------------|--------------------|-------------|
| `DockPattern` | `IDockProvider` | Used for controls that can be docked in a docking container. For example, toolbars or tool palettes. |
| `ExpandCollapsePattern` | `IExpandCollapseProvider` | Used for controls that can be expanded or collapsed. For example, menu items in an application such as the File menu. |
| `GridPattern` | `IGridProvider` | Used for controls that support grid functionality such as sizing and moving to a specified cell. For example, the large icon view in Windows Explorer or simple tables without headers in Microsoft Word. |
| `GridItemPattern` | `IGridItemProvider` | Used for controls that have cells within grids. The individual cells should support the GridItem pattern. For example, each cell in Microsoft Windows Explorer detail view. |
| `InvokePattern` | `IInvokeProvider` | Used for controls that can be invoked, such as a button. |
| `MultipleViewPattern` | `IMultipleViewProvider` | Used for controls that can switch between multiple representations of the same set of information, data, or children. For example, a list view control where data is available in thumbnail, tile, icon, list, or detail views. |
| `RangeValuePattern` | `IRangeValueProvider` | Used for controls that have a range of values that can be applied to the control. For example, a spinner control containing years might have a range of 1900 to 2010, while another spinner control presenting months would have a range of 1 to 12. |
| `ScrollPattern` | `IScrollProvider` | Used for controls that can scroll. For example, a control that has scroll bars that are active when there is more information than can be displayed in the viewable area of the control. |
| `ScrollItemPattern` | `IScrollItemProvider` | Used for controls that have individual items in a list that scrolls. For example, a list control that has individual items in the scroll list, such as a combo box control. |
| `SelectionPattern` | `ISelectionProvider` | Used for selection container controls. For example, list boxes and combo boxes. |
| `SelectionItemPattern` | `ISelectionItemProvider` | Used for individual items in selection container controls, such as list boxes and combo boxes. |
| `TablePattern` | `ITableProvider` | Used for controls that have a grid as well as header information. For example, Microsoft Excel worksheets. |
| `TableItemPattern` | `ITableItemProvider` | Used for items in a table. |
| `TextPattern` | `ITextProvider` | Used for edit controls and documents that expose textual information. |
| `TogglePattern` | `IToggleProvider` | Used for controls where the state can be toggled. For example, check boxes and checkable menu items. |
| `TransformPattern` | `ITransformProvider` | Used for controls that can be resized, moved, and rotated. Typical uses for the Transform control pattern are in designers, forms, graphical editors, and drawing applications. |
| `ValuePattern` | `IValueProvider` | Allows clients to get or set a value on controls that do not support a range of values. For example, a date time picker. |
| `WindowPattern` | `IWindowProvider` | Exposes information specific to windows, a fundamental concept to the Microsoft Windows operating system. Examples of controls that are windows are top-level application windows (Microsoft Word, Microsoft Windows Explorer, and so on), multiple-document interface (MDI) child windows, and dialogs. |

### See also

- UI Automation Control Patterns for Clients
- Control Pattern Mapping for UI Automation Clients
- UI Automation Overview
- UI Automation Properties for Clients
- UI Automation Events for Clients

---

## UI Automation Properties Overview

*Article • 09/15/2021*

> **Note**  
> This documentation is intended for .NET Framework developers who want to use the managed UI Automation classes defined in the `System.Windows.Automation` namespace. For the latest information about UI Automation, see [Windows Automation API: UI Automation](https://docs.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32).

UI Automation providers expose properties on Microsoft UI Automation elements. These properties enable UI Automation client applications to discover information about pieces of the user interface (UI), especially controls, including both static and dynamic data.

This section gives a broad overview of Microsoft UI Automation properties. More specific information is given in the following topics:

- UI Automation Properties for Clients
- Server-Side UI Automation Provider Implementation

### Property Identifiers

Every property is identified by a number and a name. The names of properties are used only for debugging and diagnosis. Providers use the numeric IDs to identify incoming property requests. Client applications, however, only use `AutomationProperty`, which encapsulates the number and name, to identify properties they wish to retrieve.

`AutomationProperty` objects representing particular properties are available as fields in various classes. For security reasons, UI Automation providers obtain these objects from a separate set of classes that are contained in `Uiautomationtypes.dll`.

The following table categorizes properties by the classes that contain the `AutomationProperty` IDs.

| Kinds of properties | Clients get IDs from | Providers get IDs from |
|---------------------|----------------------|------------------------|
| Properties common to all elements | `AutomationElement` | `AutomationElementIdentifiers` |

---

**Note on completeness:** The source PDF contains 414 pages. The content above covers the material provided in the first approximately 31 pages of the document (up through the beginning of the Properties Overview section). The remaining sections of the original document (UI Automation Events Overview, UI Automation Security Overview, detailed control pattern and control type documentation, client and provider implementation guides, and related topics) are not included in the supplied page extracts.